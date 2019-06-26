#!/usr/bin/env python3
# encoding=utf8
"""Stack definition for ennio."""
from datetime import datetime
import functools
import logging
import os

from botocore.exceptions import ClientError

from .utils import format_changes, sleep, EmptyChangeSetError, LazyBoto3Client


class EnnioStack:
    """Represents a cloudformation stack."""

    cfn = LazyBoto3Client("cloudformation")
    ssm = LazyBoto3Client("ssm")
    log = LazyBoto3Client("logs")

    def __init__(self, app, stack_config):
        self.app = app
        self.config = stack_config
        self.name = stack_config["name"]
        self.namespace = app.namespace

    @property
    @functools.lru_cache(maxsize=32)
    def stack_name(self):
        """Cloudformation stack name."""
        if self.config.get("account_unique", False):
            parts = [self.app.name, self.name]
        else:
            parts = [self.app.namespace, self.name]
        return "-".join(parts)

    @property
    @functools.lru_cache(maxsize=32)
    def resource(self):
        resource_ = {}
        paginator = self.cfn.get_paginator("list_stack_resources")
        for page in paginator.paginate(StackName=self.stack_name):
            for output in page["StackResourceSummaries"]:
                resource_[output["LogicalResourceId"]] = output[
                    "PhysicalResourceId"
                ]
        return resource_

    ############################################################################
    # Public APIs
    #
    # signature of these methods are stable and will not break, it is encouraged
    # to use these APIs so as to get the best practice in your build pipelines.
    ############################################################################
    def stack_exists(self):
        """Check whether this stack exists."""
        try:
            self.cfn.describe_stacks(StackName=self.stack_name)
            return True
        except ClientError as error:
            code = error.response["Error"]["Code"]
            message = error.response["Error"]["Message"]
            if code == "ValidationError" and message.endswith("does not exist"):
                return False
            raise

    @property
    @functools.lru_cache(maxsize=32)
    def get_stack_resource(self, stack_name, logical_name):
        """Get the pri of a resource by its logical_name in a stack."""
        response = self.cfn.describe_stack_resource(
            StackName=stack_name, LogicalResourceId=logical_name
        )
        return response["StackResourceDetail"]["PhysicalResourceId"]

    def get_stack_ssm(self):
        """Get all parameters created in this stack."""
        parameters = self.ssm.get_parameters_by_path(
            Path=f"/apps/{self.stack_name}/", WithDecryption=True
        )["Parameters"]
        return {p["Name"].split("/")[-1]: p["Value"] for p in parameters}

    def create_changeset(self, template, params):
        """Create a changeset."""
        name = f"{self.stack_name}-{datetime.now().strftime('%F-%H-%M-%S')}"
        kwargs = {
            "StackName": self.stack_name,
            "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_AUTO_EXPAND"],
            "Parameters": [
                {"ParameterKey": param, "ParameterValue": params[param]}
                for param in params
            ],
            "Tags": self.app.tags,
            "ChangeSetName": name,
            "ChangeSetType": "UPDATE" if self.stack_exists() else "CREATE",
        }
        if os.path.isfile(template):
            with open(template) as fobj:
                kwargs["TemplateBody"] = fobj.read()
        elif template.startswith("http"):
            kwargs["TemplateURL"] = template
        else:
            raise RuntimeError(f"Bad template: {template}.")
        logging.info(f"Creating changeset {name}.")
        self.cfn.create_change_set(**kwargs)
        return name

    def describe_changeset(self, name):
        """Wait till a changeset is available and return it's changes."""
        kwargs = {"ChangeSetName": name, "StackName": self.stack_name}

        start = datetime.now()
        while True:
            # Change set should be ready within seconds.
            sleep(start, 60)
            response = self.cfn.describe_change_set(**kwargs)
            status = response["Status"]
            exec_status = response["ExecutionStatus"]

            if status == "FAILED":
                reason = response["StatusReason"]
                # It just happened that AWS can give two reasons for this.
                if "didn't contain changes" in reason:
                    raise EmptyChangeSetError
                if reason == "No updates are to be performed.":
                    raise EmptyChangeSetError
                raise RuntimeError(
                    f"Failed to create changeset for {self.stack_name}: "
                    f"{reason}"
                )

            if status == "CREATE_COMPLETE" and exec_status == "AVAILABLE":
                if not response.get("NextToken"):
                    return response["Changes"]
                changes = response["Changes"]
                kwargs["NextToken"] = response["NextToken"]
                break
            logging.info(f"Status of changeset is `{status}`.")

        while True:
            response = self.cfn.describe_change_set(**kwargs)
            changes += response["Changes"]
            if not response.get("NextToken"):
                break
            kwargs["NextToken"] = response["NextToken"]
        return changes

    def execute_changeset(self, name, timeout):
        """Execute a changeset."""
        logging.info(f"Executing changeset `{name}`.")
        self.cfn.execute_change_set(
            ChangeSetName=name, StackName=self.stack_name
        )

        start = datetime.now()
        while True:
            sleep(start, timeout)
            response = self.cfn.describe_stacks(StackName=self.stack_name)
            status = response["Stacks"][0]["StackStatus"]
            if status.endswith("FAILED") or status.endswith("COMPLETE"):
                break
            logging.info(f"Waiting till stack operation completes: {status}.")

        logging.info(f"Stack operation finished: {status}")
        bad_statuses = [
            "UPDATE_ROLLBACK_COMPLETE",
            "ROLLBACK_COMPLETE",
            "DELETE_COMPLETE",
        ]
        if status in bad_statuses:
            raise RuntimeError("Failed to create/update stack.")

    def deploy_stack(self, template, params=None, timeout=3600):
        """Deploy stack changes by creating a changeset."""
        logging.info(f"Building/Updating {self.name} stack.")
        if params is None:
            params = {}
        name = self.create_changeset(template, params)
        try:
            changes = self.describe_changeset(name)
        except EmptyChangeSetError:
            logging.info(f"No change in {self.stack_name} stack.")
            return
        logging.info(
            f"Changes in changeset `{name}`: \n{format_changes(changes)}"
        )
        self.execute_changeset(name, timeout)
        return changes

    def delete_stack(self):
        """Remove this stack."""
        if not self.stack_exists():
            # If stack does not exists, ignore it.
            logging.info(f"Stack {self.stack_name} does not exists.")
            return

        response = self.cfn.describe_stacks(StackName=self.stack_name)
        stack_id = response["Stacks"][0]["StackId"]
        logging.info(f"Removing stack: {stack_id}.")
        self.cfn.delete_stack(StackName=stack_id)

        start = datetime.now()
        while True:
            sleep(start)
            response = self.cfn.describe_stacks(StackName=stack_id)
            status = response["Stacks"][0]["StackStatus"]
            if status.endswith("FAILED") or status.endswith("COMPLETE"):
                break
            logging.info(f"Waiting till stack operation completes: {status}.")

        if status != "DELETE_COMPLETE":
            raise RuntimeError("Failed to delete stack.")
        logging.info(f"Stack Removed: {stack_id}.")
        return stack_id

    def rollback(self, build):
        """
        Rollback a stack to a previous version.

        As build is the only parameter we need for a deployment,
        rolling back is done by deploying the previous version.
        """
        self.deploy(build)

    ############################################################################
    # commands
    ############################################################################
    def delete(self):
        """Remove a stack."""
        self.delete_stack()

    def deploy(self, build):
        """
        Deploy a stack.

        The inheriting class must explicitly define this method.
        """
        raise NotImplementedError
