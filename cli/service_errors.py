"""Actionable errors from current-user OS service controls."""


class ServiceCommandError(RuntimeError):
    """OS registration is missing, unsafe or owned by another installation."""
