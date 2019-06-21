#!/usr/bin/env python3
# encoding=utf8
"""Utility functions in Ennio."""
from datetime import datetime
from functools import wraps
import logging
import os
import sys
import time

import boto3
from botocore.exceptions import NoCredentialsError, ClientError


def setup_logging():
    """Logging setup"""
    logging_kwargs = {
        "stream": sys.stdout,
        "format": "[%(asctime)s][%(levelname)s] %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    }
    if "ENNIO_DEBUG" not in os.environ:
        # To enable debugging output, set DEBUG to any value in env var.
        logging.getLogger("boto3").setLevel(logging.WARNING)
        logging.getLogger("botocore").setLevel(logging.WARNING)
        logging.basicConfig(level=logging.INFO, **logging_kwargs)
    else:
        logging.basicConfig(level=logging.DEBUG, **logging_kwargs)


def sleep(start, timeout=None):
    """sleep with increasing intervals."""
    since_start = (datetime.now() - start).seconds

    if timeout is not None:
        if since_start > timeout:
            raise RuntimeError(f"Operation timeout in {timeout} seconds.")

    interval = int((since_start ** 0.5) * 2.5) + 4
    logging.debug(f"Sleeping {interval} seconds.")
    time.sleep(interval)


def require_aws(func):
    """Decorator to verify AWS session."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if not hasattr(boto3, "caller_identity"):
            try:
                boto3.setup_default_session(region_name="ap-southeast-2")
                client = boto3.client("sts")
                # We add an attibute `caller_identity` to the boto3 object,
                # this will later be used in `EnnioApplication.account_id`.
                boto3.caller_identity = client.get_caller_identity()
            except (NoCredentialsError, ClientError):
                print("Please login to AWS.", file=sys.stderr)
                sys.exit(1)
        return func(*args, **kwargs)

    return wrapper


def format_changes(changes):
    """Format changes so it will look better."""
    parts = []
    logging.debug(f"Detailed changes: {changes}")
    for change in changes:
        change_ = change["ResourceChange"]
        line = (
            f"[{change_['Action'].upper()}] "
            f"{change_['LogicalResourceId']}({change_['ResourceType']})"
        )
        if change_["Details"]:
            line += f":\n\t{change_['Details']}"
        parts.append(line)
    return "\n".join(parts)


class EmptyChangeSetError(BaseException):
    """Raised when no change needed during stack updates."""


class InvalidConfigError(BaseException):
    """Raised when we have an invalid config file."""


class LazyBoto3Client:
    """A lazy boto3 client so we will only create the client when we use it."""

    def __init__(self, name):
        self.name = name
        self.client = None

    @require_aws
    def __get__(self, obj, *args):
        if self.client is None:
            self.client = boto3.client(self.name)
        return self.client
