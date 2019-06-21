#!/usr/bin/env python3
# encoding=utf8
"""
Model definition for ennio.

Predefined commands:

compile: generate templates.
deploy: deploy specified stack
delete: delete specified stack
"""
from pathlib import Path
import argparse
import importlib
import inspect
import logging
import os
import sys

from botocore.exceptions import ClientError
import yaml

from .utils import InvalidConfigError, LazyBoto3Client


display_name = lambda name: name.replace("_", "-")
method_name = lambda name: name.replace("-", "_")


class EnnioConfig:
    """Represents a configuration yaml file for ennio."""

    def __init__(self, conf_file):
        conf_file = Path(conf_file)
        logging.debug(f"Reading conf file: {conf_file.resolve()}.")
        with open(conf_file) as fobj:
            self.data = yaml.load(fobj, Loader=yaml.SafeLoader)

        self.stacks = [stack["name"] for stack in self.data["stacks"]]
        self.prefixes = self.stacks + ["application"]
        self.validate()
        self.set_defaults()

    def __getitem__(self, name):
        """Pass the dict lookup to self.data."""
        return self.data[name]

    def validate(self):
        """Validate our DSL."""
        mandatory = ["application", "stacks", "deploy-steps"]
        if any(field not in self.data for field in mandatory):
            raise InvalidConfigError("Mandatory field missing in config.")

        if "name" not in self.data["application"]:
            raise InvalidConfigError(f"Undefined application name.")

        for step in self.data["deploy-steps"]:
            if not self.validate_step(step):
                raise InvalidConfigError(f"Invalid step defined: {step}.")

        extra_commands = self.data.get("extra-commands", [])
        for command in extra_commands:
            if not self.is_valid_method(command):
                raise InvalidConfigError(f"Invalid command defined: {command}.")

    def is_valid_method(self, method):
        """Return whether a method found in config is valid."""
        if "." not in method:
            return False

        if method.count(".") != 1:
            return False

        stack, _ = method.split(".")
        return stack in self.prefixes

    def validate_step(self, step):
        """Validate a single step in `deploy-steps` list."""
        if "stack" not in step and "operation" not in step:
            # miss both
            return False

        if "stack" in step and "operation" in step:
            # have both
            return False

        if "operation" in step and "." not in step["operation"]:
            # invalid operation specification.
            return False

        if "stack" in step:
            # return green if stack name found.
            return step["stack"] in self.stacks
        else:
            return self.is_valid_method(step["operation"])

    def set_defaults(self):
        """Setup default values for config."""
        for step in self.data["deploy-steps"]:
            step.setdefault("ignore_error", False)


class EnnioApplication:
    """Represents an application that has several cfn stacks as components."""

    NO_VERSION = "-1"

    cfn = LazyBoto3Client("cloudformation")
    ssm = LazyBoto3Client("ssm")

    def __init__(self, conf_file):
        self.config = EnnioConfig(conf_file)
        self.name = self.config["application"]["name"]
        self.namespace = os.environ.get("NAMESPACE", self.name)
        self.bucket = self.config["application"]["bucket"]
        self.yaml_tags = self.config["application"]["tags"]

        self.stacks = {}
        for config in self.config["stacks"]:
            mod_str, klass = config["class"].rsplit(".", 1)
            if mod_str not in sys.modules:
                mod = importlib.import_module(mod_str)
            stack_class = getattr(mod, klass)
            self.stacks[config["name"]] = stack_class(self, config)

        self.steps = self.parse_steps()
        self.extra_commands = {
            cmd.split(".")[1]: self.get_method(cmd)
            for cmd in self.config["extra-commands"]
        }

        self._version = None

    def parse_steps(self):
        """Parse the `deploy-steps` section in the config."""
        steps = []
        for config in self.config["deploy-steps"]:
            step = {}
            if "stack" in config:
                step = self.parse_stack_step(config)
            else:
                step = self.parse_operation_step(config)
            steps.append(step)
        return steps

    def get_method(self, method):
        """Get the method from method specification."""
        stack_name, name = method.split(".")
        if stack_name == "application":
            instance = self
        else:
            instance = self.stacks[stack_name]
        if not hasattr(instance, method_name(name)):
            raise InvalidConfigError(f"Invalid method `{method}` in config.")
        return getattr(instance, method_name(name))

    def parse_stack_step(self, config):
        """Parse step when the step involves a cloudformation stack."""
        no_op = lambda *args, **kwargs: logging.info("No operations performed.")
        stack = self.stacks[config["stack"]]

        step = {
            "name": config["stack"],
            "deploy": stack.deploy,
            "rollback": stack.rollback,
            "delete": stack.delete,
            "ignore_error": config["ignore_error"],
        }

        if os.environ.get("ENNIO_DELETE_ALL", "false").lower() == "true":
            step["delete"] = stack.delete
        elif "on_delete" in config:
            on_delete = config["on_delete"]
            if on_delete == "pass":
                step["delete"] = no_op
            else:
                step["delete"] = self.get_method(on_delete)
        return step

    def parse_operation_step(self, config):
        """Parse step when the step is an operation."""
        no_op = lambda *args, **kwargs: logging.info("No operations performed.")
        method = self.get_method(config["operation"])

        step = {
            "name": config["operation"],
            "deploy": method,
            "rollback": method,
            "delete": no_op,
            "ignore_error": config["ignore_error"],
        }
        if "on_delete" in config:
            on_delete = config["on_delete"]
            if on_delete == "pass":
                step["delete"] = no_op
            else:
                step["delete"] = self.get_method(on_delete)
        return step

    ##############################################
    # commandline entry point
    ##############################################
    def main(self):
        """
        Entry point.

        Parse command line arguments, find the method either from application
        class or a stack class, pass key word arguments to it and make it happen
        """
        parsed = self.parse_args()

        method = self.sub_commands[parsed.command]

        if not callable(method) and isinstance(method, str):
            print(method)
            return

        params = inspect.signature(method).parameters
        for key, value in params.items():
            if key not in parsed and value is not value.empty:
                raise argparse.ArgumentTypeError(
                    f"`{parsed.command}` need argument `--{display_name(key)}`."
                )
        kwargs = {
            name: getattr(parsed, name)
            for name in dir(parsed)
            if ((not name.startswith("_")) and name != "command")
        }
        method(**kwargs)

    def parse_args(self):
        """
        Argparser that can dynamically require arguments.

        We need to parse commandline args for different commands, these commands
        might be requiring different arguments. So we parse the command first,
        get the signature of the method, and add the parameters dynamically.
        """
        parser = argparse.ArgumentParser(
            usage=f"usage: {sys.argv[0]} [-h] command [options]"
        )
        actions = ", ".join(sorted(list(self.sub_commands.keys())))
        parser.add_argument(
            "command",
            help=f"Action to be carried out, Valid actions are: {actions}",
            metavar="command",
            choices=self.sub_commands,
        )
        parsed, unknown = parser.parse_known_args()
        for arg in unknown:
            if arg.startswith("--"):
                parser.add_argument(arg)
        return parser.parse_args()

    @property
    def sub_commands(self):
        """
        Only show a subset of all available commands.

        Each of the method defined in EnnioApplication is exposed to the
        commandline as sub commands. More over, parse_args would allow
        properties that return string to be called. However, it would be
        confusing to show all these properties and subcommand in the help
        message, so we need to filter here.
        """
        commands = {
            "delete-all": self.delete_all,
            "deploy-all": self.deploy_all,
        }
        for stack_name, stack in self.stacks.items():
            commands[f"deploy-{stack_name}"] = stack.deploy
            commands[f"delete-{stack_name}"] = stack.delete

        commands.update(self.extra_commands)
        return commands

    ##############################################
    # application version
    ##############################################
    @property
    def version_parameter(self):
        """
        Subclass should define a property named `namespace` for this to work.
        """
        return f"/application-versions/{self.namespace}"

    @property
    def version(self):
        """
        Get the version of the meta stack, return -1 if the version is not set.

        Meta stack means the stack(s) that are used by the application.
        For example, a typical ecs application has about 4 or more
        Cloudformation stacks, all these stacks is part of the meta stack.

        Subclass should define a property named `namespace` for this to work.
        """
        if self._version is None:
            logging.info(f"Getting version from SSM: {self.version_parameter}.")
            try:
                self._version = self.ssm.get_parameter(
                    Name=self.version_parameter
                )["Parameter"]["Value"]
            except ClientError as error:
                if error.response["Error"]["Code"] == "ParameterNotFound":
                    self._version = self.NO_VERSION
                else:
                    raise error
        logging.info(f"Got stack version: {self._version}.")
        return self._version

    @version.setter
    def version(self, _version):
        """
        Set the version of the meta stack.

        Subclass should define a property named `namespace` for this to work.
        """
        logging.info(f"Setting stack version: {_version}.")
        self.ssm.put_parameter(
            Name=self.version_parameter,
            Value=_version,
            Type="String",
            Overwrite=True,
        )
        self._version = _version

    @version.deleter
    def version(self):
        """
        Remove the version of the meta stack.

        Subclass should define a property named `namespace` for this to work.
        """
        logging.info(f"Removing stack version.")
        try:
            self.ssm.delete_parameter(Name=self.version_parameter)
            logging.info("Version removed from parameter store.")
        except ClientError as error:
            if not error.response["Error"]["Code"] == "ParameterNotFound":
                raise
            logging.info("Version does not exist in parameter store.")
        self._version = None

    ##############################################
    # Properties that can be used.
    ##############################################

    @property
    def tags(self):
        tags_ = self.yaml_tags
        return [{"Key": tag, "Value": tags_[tag]} for tag in tags_]

    ##############################################
    # Helper methods
    ##############################################
    def rollback_all(self, changed):
        """Rollback all changed stacks."""
        if self.version == self.NO_VERSION:
            logging.warning(f"Deploying for the first time, no rollback.")
            return

        steps = [step["name"] for step in changed]
        logging.info(f"Rolling back changes: {steps}.")
        for step in reversed(changed):
            logging.debug(f"running step: {step}")
            name = step["name"]
            logging.info(f"{name} rollback step started.")
            step["rollback"](self.version)
            logging.info(f"{name} rollback step finished.")
        logging.info(f"Rollback completed successfully.")

    ##############################################
    # Operations
    ##############################################
    def deploy_all(self, build):
        """Update all stacks in a transaction."""
        logging.info(f"Deploying {build}, current version: {self.version}.")

        changed = []
        for step in self.steps:
            logging.debug(f"running step: {step}")
            name = step["name"]
            try:
                logging.info(f"{name} deploy step started.")
                step["deploy"](build)
                logging.info(f"{name} deploy step finished.")
                changed.append(step)
            except Exception as err:
                logging.warning(f"{name} deploy step failed with: {err}")
                if step["ignore_error"]:
                    # Not going to add this step to changed, because we failed
                    # to change it.
                    logging.warning(f"Ignoring error for {name}. Error: {err}")
                    continue
                break
        else:
            # No break from for loop, all good.
            self.version = build
            logging.info(
                f"Deployment of application {self.name} completed successfully."
            )
            return

        if os.environ.get("ENNIO_NO_ROLLBACK", "false").lower() == "true":
            logging.warning(f"Rollback canceled by env var.")
        else:
            logging.warning(f"Reverting to build {self.version}.")
            self.rollback_all(changed)
        sys.exit(1)

    def delete_all(self):
        """Delete all stacks one by one."""
        logging.info(f"Removing stacks, current version: {self.version}.")

        for step in reversed(self.steps):
            logging.debug(f"running step: {step}")
            name = step["name"]
            try:
                logging.info(f"{name} delete step started.")
                step["delete"]()
                logging.info(f"{name} delete step finished.")
            except Exception as err:
                logging.warning(f"{name} delete step failed with: {err}")
                break

    def compile_all(self):
        """Compile all cfn templates and validate them all."""
