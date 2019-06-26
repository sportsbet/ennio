from setuptools import find_packages, setup


VERSION = '0.1.1'


if __name__ == "__main__":
    setup(
        name="ennio",
        version=VERSION,
        author="Sportsbet",
        author_email="engineering@sportsbet.com.au",
        license="Apache-2.0",
        url="https://github.com/sportsbet/ennio",
        description="Interstack orchestration tool for AWS CloudFormation",
        packages=find_packages(),
        install_requires=["PyYAML", "boto3", "jinja2"],
    )
