<p align="center">
  <img src="https://github.com/fast-aircraft-design/FAST-OAD/blob/master/docs/img/FAST_OAD_logo2.jpg?raw=true" alt="FAST-OAD logo" width="600">
</p>

# Life Cycle Assessment plug-in for FAST-OAD


[![image](https://img.shields.io/pypi/v/fast-oad-lca.svg)](https://pypi.python.org/pypi/fast-oad-lca)
[![image](https://img.shields.io/pypi/pyversions/fast-oad-lca.svg)](https://pypi.python.org/pypi/fast-oad-lca)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

![Tests](https://github.com/fast-aircraft-design/FAST-OAD_LCA/workflows/Tests/badge.svg)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/06d1fb8ee5c3429cb3cbb165413187bc)](https://app.codacy.com/gh/fast-aircraft-design/FAST-OAD_LCA/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)[![codecov](https://codecov.io/gh/fast-aircraft-design/FAST-OAD_CS25/branch/main/graph/badge.svg?token=91CIX996RD)](https://codecov.io/gh/fast-aircraft-design/FAST-OAD_CS25)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

[![Documentation Status](https://readthedocs.org/projects/fast-oad-lca/badge/?version=stable)](https://fast-oad-lca.readthedocs.io/)
[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/fast-aircraft-design/FAST-OAD_LCA.git/latest-release?urlpath=lab%2Ftree%2Fsrc%2Ffastoad_lca%2Fnotebooks)

This package is a plugin for FAST-OAD. It contains models for the Life Cycle Assessment (LCA) of aircraft.
To use the full capabilities of this package, you need a valid [ecoinvent](https://ecoinvent.org/) license.

## Install

**Prerequisite**: This plug-in for FAST-OAD needs at least **Python 3.10**.

It is recommended (but not required) to do the install in a virtual
environment ([conda](https://docs.conda.io/en/latest/),
[venv](https://docs.python.org/3.10/library/venv.html), ...)

Once Python is installed, installation can be done using pip. FAST-OAD, the core software, will be
installed at the same time.

> **Note**: If your network uses a proxy, you may have to do [some
> settings](https://pip.pypa.io/en/stable/user_guide/#using-a-proxy-server)
> for pip to work correctly

You can install the latest version (including the main software FAST-OAD) with this command:

``` {.bash}
$ pip install --upgrade fast-oad-lca
```
