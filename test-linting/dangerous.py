"""Throwaway file to exercise semgrep CI findings."""

import subprocess


def run_tool(tool_name: str) -> None:
    subprocess.run(tool_name, shell=True)


def evaluate(expr: str) -> object:
    return eval(expr)
