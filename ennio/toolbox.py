#!/usr/bin/env python3
# encoding=utf8
"""
Toolbox for ennio.

This file/module is for generic AWS helpers.
"""
import logging

from botocore.exceptions import ClientError
import boto3


def clean_log_groups(stack_id):
    """
    Ensure that all log groups in a removed stack are removed.

    Sounds silly but we've seen it happen before. For example, when we have
    defined a log group that goes with a lambda function, cfn will delete
    the log group first(because log group depends on the lambda), and at
    that time, if we have some requests goes to lambda, we will see a new
    log group created by AWS which will stay there and bug us when we want
    to deploy the stack again.
    """
    cfn_cli = boto3.client("cloudformation")
    log_cli = boto3.client("logs")

    paginator = cfn_cli.get_paginator("list_stack_resources")
    for response in paginator.paginate(StackName=stack_id):
        for resource in response["StackResourceSummaries"]:
            if resource["ResourceType"] != "AWS::Logs::LogGroup":
                continue
            log_group = resource["PhysicalResourceId"]
            try:
                log_cli.delete_log_group(logGroupName=log_group)
                # If the line above does not trigger an exception, we have
                # actually removed a log group. Log it down for reference.
                logging.info(f"Removed log group: {log_group}")
            except ClientError as err:
                code = err.response["Error"]["Code"]
                if code == "ResourceNotFoundException":
                    # log group not found, this is actually the expected
                    # behaviour.
                    continue
                raise
