from setuptools import setup, find_packages

setup(
    name="invoicesentinel",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "invoicesentinel=invoicesentinel.cli:entry_point",
        ],
    },
    install_requires=[
        "httpx>=0.28.0",
        "pyyaml>=6.0",
        "streamlit>=1.58.0",
        "typer>=0.26.0",
        "watchdog>=6.0.0",
    ],
    python_requires=">=3.11",
)
