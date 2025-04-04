"""Compilation and evaluation of LCA model."""
#  This file is part of FAST-OAD_LCA
#  Copyright (C) 2023 ONERA & ISAE-SUPAERO
#  FAST is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

import openmdao.api as om
import fastoad.api as oad
import numpy as np
import brightway2 as bw
import pandas as pd
import lca_algebraic as agb
from lca_algebraic.axis_dict import AxisDict
from typing import Dict
from lca_modeller.io.configuration import LCAProblemConfigurator, KEY_YEAR
import re
import os
import sympy as sym
from .constants import LCA_CHARACTERIZATION_KEY, LCA_NORMALIZATION_KEY, LCA_WEIGHTING_KEY, LCA_FACTOR_KEY, LCA_SINGLE_SCORE_KEY

import logging
_LOGGER = logging.getLogger(__name__)


@oad.RegisterOpenMDAOSystem("fastoad.plugin.lca")
class LifeCycleAssessment(om.Group):
    """
    Group for LCA models and calculations.
    """

    def initialize(self):
        self.options.declare("configuration_file", default=None, types=str)  # Path to LCA configuration file
        self.options.declare("axis", default=None, types=str)  # Axis to ventilate results by e.g. life-cycle phase
        self.options.declare("normalization", default=False, types=bool)  # Normalize results
        self.options.declare("weighting", default=False, types=bool)  # Weight and aggregate results
        self.options.declare("nonfloat_params", default=None, types=dict)  # Values for non-float parameters of LCA

    def setup(self):
        # Core LCA (model + characterisation)
        self.add_subsystem(
            "core",
            LCAcore(
                configuration_file=self.options["configuration_file"],
                axis=self.options["axis"],
                nonfloat_params=self.options["nonfloat_params"]
            ),
            promotes=["*"]
        )

        # NORMALIZATION
        if self.options["weighting"] or self.options["normalization"]:
            self.add_subsystem("normalization", Normalization())

        # WEIGHTING AND AGGREGATION
        if self.options["weighting"]:
            self.add_subsystem("weighting", Weighting())
            self.add_subsystem("aggregation", Aggregation())

    def configure(self):
        """
        Configure() method is called after setup() of the containing group to get access to `model` metadata and
        set the appropriate inputs/outputs of the normalization and weighting modules.
        """
        if self.options["weighting"] or self.options["normalization"]:
            methods = []
            axis_keys = []
            for var_in in self.core.list_outputs(return_format='dict', out_stream=None).keys():
                var_out_norm = var_in.replace(LCA_CHARACTERIZATION_KEY, LCA_NORMALIZATION_KEY)
                m_name = var_in.split(":")[2]  # get method name (not very generic, but works for now)
                norm_factor = LCA_NORMALIZATION_KEY + m_name + LCA_FACTOR_KEY

                # Add outputs from core LCIA as inputs to normalization
                self.normalization.add_input(var_in, val=np.nan, units=None)
                self.normalization.add_output(var_out_norm, units=None)
                self.normalization.declare_partials(var_out_norm, var_in, method="exact")

                # Add normalization factor as input
                if m_name not in methods:
                    self.normalization.add_input(norm_factor, val=np.nan, units=None)
                    self.normalization.declare_partials(var_out_norm, norm_factor, method="exact")

                if self.options["weighting"]:
                    var_out_weight = var_out_norm.replace(LCA_NORMALIZATION_KEY, LCA_WEIGHTING_KEY)
                    weight_factor = LCA_WEIGHTING_KEY + m_name + LCA_FACTOR_KEY

                    # Add outputs from normalization as inputs to weighting
                    self.weighting.add_input(var_out_norm, val=np.nan, units=None)
                    self.weighting.add_output(var_out_weight, units=None)
                    self.weighting.declare_partials(var_out_weight, var_out_norm, method="exact")

                    # Add weighting factor as input
                    if m_name not in methods:
                        self.weighting.add_input(weight_factor, val=1.0, units=None)
                        self.weighting.declare_partials(var_out_weight, weight_factor, method="exact")

                    # Add outputs from weighting as inputs to aggregation (single score)
                    self.aggregation.add_input(var_out_weight, val=np.nan, units=None)

                    # get axis key (not very generic, but works for now)
                    axis_key = ":" + ":".join(var_in.split(":")[3:]) if len(var_in.split(":")) > 3 else ""
                    var_single_score = LCA_SINGLE_SCORE_KEY + axis_key
                    if axis_key not in axis_keys:
                        self.aggregation.add_output(var_single_score, val=np.nan, units=None)
                        axis_keys.append(axis_key)
                    self.aggregation.declare_partials(var_single_score, var_out_weight, val=1.0)

                if m_name not in methods:
                    methods.append(m_name)

            # Promote variables
            self.promotes("normalization", any=['*'])
            if self.options["weighting"]:
                self.promotes("weighting", any=['*'])
                self.promotes("aggregation", any=['*'])


class LCAcore(om.ExplicitComponent):
    """
    This OpenMDAO component implements an LCA model using brightway2 and lca_algebraic libraries.
    It creates an LCA model from a configuration file, then compiles functions for the impacts and partial derivatives.
    The parametric functions (lambdas) are used to compute the impacts and partial derivatives of the impacts w.r.t.
    parameters in a very fast way compared to conventional LCA.
    """

    # Cache for storing LCA model, methods and lambdas.
    # This avoids recompiling everything if already done in previous setup of FAST-OAD
    # One downside is that the cache is shared between all instances of LCAcore, so that only one LCA model can be used
    _cache = {}

    def initialize(self):
        # Declare options
        self.options.declare("configuration_file", default=None, types=str)
        self.options.declare("axis", default=None, types=str)
        self.options.declare("nonfloat_params", default=None, types=dict)

    def setup(self):
        # Check if cache is empty or if configuration file has been modified
        last_mod_time = os.path.getmtime(self.options['configuration_file'])
        if not LCAcore._cache or last_mod_time > LCAcore._cache.get('last_mod_time', 0):
            _LOGGER.info("LCA module: No cache found or configuration file has been modified. "
                         "Compiling LCA model and functions.")
            LCAcore._cache['last_mod_time'] = last_mod_time

            # Load LCA configuration file, build model and get LCIA methods
            _, model, methods = LCAProblemConfigurator(self.options['configuration_file']).generate()

            print("Compiling LCIA functions")
            # Compile expressions for impacts
            lambdas = agb.lca._preMultiLCAAlgebric(model, methods, axis=self.options['axis'])
            # TODO: enable multiple axes to be declared, e.g. to ventilate impacts both by phase and component.

            # Compile expressions for partial derivatives of impacts w.r.t. parameters
            partial_lambdas_dict = _preMultiLCAAlgebricPartials(model, methods, axis=self.options['axis'])
            print("LCIA functions successfully compiled")

            # Set cache
            LCAcore._cache['model'] = model
            LCAcore._cache['methods'] = methods
            LCAcore._cache['lambdas'] = lambdas
            LCAcore._cache['partial_lambdas_dict'] = partial_lambdas_dict

        else:
            _LOGGER.info("Loading cached data for LCA")

        # Get cached values
        self.model = LCAcore._cache['model']
        self.methods = LCAcore._cache['methods']
        self.lambdas = LCAcore._cache['lambdas']
        self.partial_lambdas_dict = LCAcore._cache['partial_lambdas_dict']

        # Get axis keys to ventilate results by e.g. life-cycle phase
        self.axis_keys = self.lambdas[0].axis_keys

        # Dict for storing LCA parameters and their values
        self.parameters = dict()

        # Declare LCA parameters as inputs
        for p in agb.all_params().values():
            if p.type == 'float':
                p_name = p.name.replace('__', ':')  # refactor names (':' is not supported in LCA parameters)
                if p_name == KEY_YEAR:
                    p_name = 'lca:parameters:' + p_name
                    self.add_input(p_name, val=p.default, units=None)
                else:
                    self.add_input(p_name, val=np.nan, units=p.unit)
            elif p.name in self.options['nonfloat_params']:
                self.parameters[p.name] = self.options['nonfloat_params'][p.name].replace('-', '_')
            else:
                _LOGGER.warning("LCA parameter '%s' not provided in configuration file. Default value will be applied: '%s'" % (
                p.name, p.default))

        # Declare outputs for each method and axis key
        for m in self.methods:
            m_name = bw_to_fastoad_lcia_name(m)
            m_unit = bw.Method(m).metadata['unit'] + "/FU" if bw.Method(m).metadata['unit'] else None
            self.add_output(LCA_CHARACTERIZATION_KEY + m_name,
                            units=None,  # NB: LCA units not supported by OpenMDAO so set in description
                            desc=m_unit)
            if self.axis_keys:
                for axis_key in self.axis_keys:
                    self.add_output(LCA_CHARACTERIZATION_KEY + m_name + ':' + axis_key,
                                    units=None,
                                    desc=bw.Method(m).metadata['unit'] + "/FU")

    def setup_partials(self):
        self.declare_partials("*", "*", method="exact")
        # TODO: distinguish between constant and variable parameters to avoid unnecessary partials calculations

    def compute(self, inputs, outputs):
        # Dict containing str parameters (constant)
        parameters = self.parameters.copy()
        # Add float parameters (variable)
        for name, value in inputs.items():
            if name == 'lca:parameters:' + KEY_YEAR:  # specific case for year parameter
                parameters[KEY_YEAR] = value[0]
            else:
                parameters[name.replace(':', '__')] = value[0]

        # Compute impacts from pre-compiled expressions and current parameters values
        res = self.compute_impacts_from_lambdas(
            self.lambdas,
            **parameters
        )

        # Store results in outputs
        for m in res:  # for each LCIA method
            m_name = agb_to_fastoad_lcia_name(m)
            if self.axis_keys:  # results by phase/contributor
                s = 0
                for axis_key in self.axis_keys:
                    s_i = res[m][res.index.get_level_values(self.options['axis']) == axis_key].iloc[0]
                    outputs[LCA_CHARACTERIZATION_KEY + m_name + ':' + axis_key] = s_i
                    s += s_i
                outputs[LCA_CHARACTERIZATION_KEY + m_name] = s
            else:
                outputs[LCA_CHARACTERIZATION_KEY + m_name] = res[m].iloc[0]

    def compute_partials(self, inputs, partials, discrete_inputs=None):
        # Refactor input names
        parameters = {
            name.replace(':', '__'): value[0] for name, value in inputs.items()
        }

        # Compute partials from pre-compiled expressions and current parameters values
        res = {param_name: self.compute_impacts_from_lambdas(partial_lambdas, **parameters) for param_name, partial_lambdas in
               self.partial_lambdas_dict.items()}

        # Compute partials
        for param_name, res_param in res.items():
            for m in res_param:
                # m_name = re.sub(r': |/| ', '_', m.split(' - ')[0])
                m_name = agb_to_fastoad_lcia_name(m)
                input_name = param_name.replace('__', ':')
                if self.axis_keys:  # results by phase/contributor
                    s = 0
                    for axis_key in self.axis_keys:
                        s_i = res_param[m][res_param.index.get_level_values(self.options['axis']) == axis_key].iloc[0]
                        partials[LCA_CHARACTERIZATION_KEY + m_name + ':' + axis_key, input_name] = s_i
                        s += s_i
                    partials[LCA_CHARACTERIZATION_KEY + m_name, input_name] = s
                else:
                    partials[LCA_CHARACTERIZATION_KEY + m_name, input_name] = res_param[m].iloc[0]

    def compute_impacts_from_lambdas(
        self,
        lambdas,
        **params: Dict[str, agb.SingleOrMultipleFloat],
    ):
        """
        Modified version of compute_impacts from lca_algebraic.
        More like a wrapper of _postLCAAlgebraic, to avoid calling _preLCAAlgebraic which is unecessarily
        time consuming when lambdas have already been calculated and doesn't have to be updated.
        """
        dfs = dict()

        dbname = self.model.key[0]
        with agb.DbContext(dbname):
            # Check no params are passed for FixedParams
            for key in params:
                if key in agb.params._fixed_params():
                    _LOGGER.warning("Param '%s' is marked as FIXED, but passed in parameters : ignored" % key)

            #lambdas = _preMultiLCAAlgebric(model, methods, alpha=alpha, axis=axis)  # <-- this is the time-consuming part

            df = agb.lca._postMultiLCAAlgebric(self.methods, lambdas, **params)

            model_name = agb.base_utils._actName(self.model)
            while model_name in dfs:
                model_name += "'"

            # param with several values
            list_params = {k: vals for k, vals in params.items() if isinstance(vals, list)}

            # Shapes the output / index according to the axis or multi param entry
            if self.options['axis']:
                df[self.options['axis']] = lambdas[0].axis_keys
                df = df.set_index(self.options['axis'])
                df.index.set_names([self.options['axis']])

                # Filter out line with zero output
                df = df.loc[
                    df.apply(
                        lambda row: not (row.name is None and row.values[0] == 0.0),
                        axis=1,
                    )
                ]

                # Rename "None" to others
                df = df.rename(index={None: "other"})

                # Sort index
                df.sort_index(inplace=True)

                # Add "total" line
                df.loc["*sum*"] = df.sum(numeric_only=True)

            elif len(list_params) > 0:
                for k, vals in list_params.items():
                    df[k] = vals
                df = df.set_index(list(list_params.keys()))

            else:
                # Single output ? => give the single row the name of the model activity
                df = df.rename(index={0: model_name})

            dfs[model_name] = df

        if len(dfs) == 1:
            df = list(dfs.values())[0]
        else:
            # Concat several dataframes for several models
            df = pd.concat(list(dfs.values()))

        return df


class Normalization(om.ExplicitComponent):
    """
    Normalization of the LCIA results.
    """

    def compute(self, inputs, outputs):
        for var_in in inputs:
            if LCA_FACTOR_KEY not in var_in:
                var_out = var_in.replace(LCA_CHARACTERIZATION_KEY, LCA_NORMALIZATION_KEY)
                m_name = var_in.split(":")[2]  # Not very generic, but works for now
                norm_factor = LCA_NORMALIZATION_KEY + m_name + LCA_FACTOR_KEY
                outputs[var_out] = inputs[var_in] / inputs[norm_factor]

    def compute_partials(self, inputs, partials, discrete_inputs=None):
        for var_in in inputs:
            if LCA_FACTOR_KEY not in var_in:
                var_out = var_in.replace(LCA_CHARACTERIZATION_KEY, LCA_NORMALIZATION_KEY)
                m_name = var_in.split(":")[2]  # Not very generic, but works for now
                norm_factor = LCA_NORMALIZATION_KEY + m_name + LCA_FACTOR_KEY
                partials[var_out, var_in] = 1.0 / inputs[norm_factor]
                partials[var_out, norm_factor] = -inputs[var_in] / inputs[norm_factor] ** 2


class Weighting(om.ExplicitComponent):
    """
    Weighting of the normalised LCIA results.
    """

    def compute(self, inputs, outputs):
        for var_in in inputs:
            if LCA_FACTOR_KEY not in var_in:
                var_out = var_in.replace(LCA_NORMALIZATION_KEY, LCA_WEIGHTING_KEY)
                m_name = var_in.split(":")[2]  # Not very generic, but works for now
                weight_factor = LCA_WEIGHTING_KEY + m_name + LCA_FACTOR_KEY
                outputs[var_out] = inputs[var_in] / inputs[weight_factor]

    def compute_partials(self, inputs, partials, discrete_inputs=None):
        for var_in in inputs:
            if LCA_FACTOR_KEY not in var_in:
                var_out = var_in.replace(LCA_NORMALIZATION_KEY, LCA_WEIGHTING_KEY)
                m_name = var_in.split(":")[2]  # Not very generic, but works for now
                weight_factor = LCA_WEIGHTING_KEY + m_name + LCA_FACTOR_KEY
                partials[var_out, var_in] = inputs[weight_factor]
                partials[var_out, weight_factor] = inputs[var_in]


class Aggregation(om.ExplicitComponent):
    """
    Aggregation of the weighted LCIA results into a single score.
    """

    def compute(self, inputs, outputs):
        axis_keys = []
        for var_in in inputs:
            axis_key = ":" + ":".join(var_in.split(":")[3:]) if len(var_in.split(":")) > 3 else ""
            if axis_key not in axis_keys:
                var_out = LCA_SINGLE_SCORE_KEY + axis_key
                if axis_key == "":  # single score for entire system
                    outputs[var_out] = sum(inputs[var_in] for var_in in inputs if len(var_in.split(":")) == 3)
                else:  # single scores by phase/contributor
                    outputs[var_out] = sum(inputs[var_axis] for var_axis in inputs if axis_key in var_axis)


def _preMultiLCAAlgebricPartials(model, methods, alpha=1, axis=None):
    """
    Modified version of _preMultiLCAAlgebric from lca_algebraic
    to compute partial derivatives of impacts w.r.t. parameters instead of expressions of impacts.
    """
    with agb.DbContext(model):
        exprs = agb.lca._modelToExpr(model, methods, alpha=alpha, axis=axis)

        # Replace ceiling function by identity for better derivatives
        exprs = [expr.replace(sym.ceiling, lambda x: x) for expr in exprs]

        # Lambdify (compile) expressions
        if isinstance(exprs[0], AxisDict):
            return {
                param.name: [
                    agb.lca.LambdaWithParamNames(
                        AxisDict({axis_tag: res.diff(param) for axis_tag, res in expr.items()})) for expr in exprs
                ] for param in agb.all_params().values()
            }
        else:
            return {
                param.name: [
                    agb.lca.LambdaWithParamNames(expr.diff(param)) for expr in exprs
                ]
                for param in agb.all_params().values()
            }


def agb_to_fastoad_lcia_name(method_name: str) -> str:
    """
    Convert a name from lca_algebraic to FAST-OAD_LCA naming convention.
    Used for methods names as returned by compute_impacts from lca_algebraic.
    """
    name = method_name.split('[')[0]  # remove units (keep only method name)
    name = name.replace(' - ', ':')  # replace separator
    name = name.replace(')', '').replace('(', '')  # remove parentheses
    name = re.sub(r': |/| |, ', '_', name)  # replace other elements by underscores
    name = name.rstrip('_')  # remove trailing underscores
    return name


def bw_to_fastoad_lcia_name(method_name: str) -> str:
    """
    Convert a name from lca_algebraic to FAST-OAD_LCA naming convention.
    Used for methods names expressed as tuples (brightway convention).
    """
    name = method_name[-2] + ':' + method_name[-1]  # merge last two elements of method tuple
    name = name.replace(')', '').replace('(', '')  # remove parentheses
    name = re.sub(r': |/| |, ', '_', name)  # replace other elements by underscores
    name = name.rstrip('_')  # remove trailing underscores
    return name