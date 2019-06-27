# ennio

[![image](https://img.shields.io/pypi/v/ennio.svg)](https://github.com/sportsbet/ennio)
[![image](https://img.shields.io/pypi/l/ennio.svg)](https://github.com/sportsbet/ennio)
[![image](https://img.shields.io/pypi/pyversions/ennio.svg)](https://github.com/sportsbet/ennio)

[![image](./doc/ennio-logo.png)](https://github.com/sportsbet/ennio)

Ennio is an interstack orchestration framework for AWS CloudFormation

## Installation

Use the package manager [pip](https://pip.pypa.io/en/stable/) to install foobar.

```bash
pip install ennio
```

## Why

Cloudformation alone cannot do all the infrastructure work. Frequently we need some extra operational steps between deployments of Cloudformation stacks. Moreover, we often split a huge application into several smaller stacks to minimize the maintenance effort. It is good to have a mechanism to do all the stack deployments in a single step, and possibly do rollback all the changes to a previous when a stack deployment failed. Ennio did all that for us.

This framework, like many others, are opinionated, in that:

1. We believe in reproducible deployments. For each build of infra code, we should create a bundle that can be deployed and redeployed anytime we like.
2. We had made a decision to store this bundle in S3.
3. Although we had provided a way to generate cloudformation templates using jinja2, and prefer that all templates be written in yaml, you can still bring your own way to generate all the templates.

## Example

Please take a look at the sample project in example dir.

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

Please make sure to update tests as appropriate.

## License

[Apache](https://choosealicense.com/licenses/apache/)
