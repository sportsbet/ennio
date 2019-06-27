from setuptools import find_packages, setup


VERSION = "0.1.2"


with open("README.md") as fobj:
    long_description = fobj.read()

if __name__ == "__main__":
    setup(
        name="ennio",
        version=VERSION,
        author="Sportsbet",
        author_email="engineering@sportsbet.com.au",
        license="Apache-2.0",
        url="https://github.com/sportsbet/ennio",
        description="interstack orchestration framework for AWS CloudFormation",
        long_description=long_description,
        long_description_content_type="text/markdown",
        packages=find_packages(),
        install_requires=["PyYAML", "boto3", "jinja2"],
        classifiers=[
            "Development Status :: 4 - Beta",
            "License :: OSI Approved :: Apache Software License",
            "Programming Language :: Python",
            "Programming Language :: Python :: 3",
            "Programming Language :: Python :: 3.6",
            "Programming Language :: Python :: 3.7",
        ],
    )
