from setuptools import setup

setup(
    name="fde_component",
    version="0.1.0",
    description="Resumable Ingestion, Sync & Job Orchestration (SP-05)",
    author="FDE",
    author_email="",
    packages=[
        "fde_component",
        "fde_component.jobs",
        "fde_component.doctypes",
    ],
    install_requires=["croniter"],
    zip_safe=False,
)
