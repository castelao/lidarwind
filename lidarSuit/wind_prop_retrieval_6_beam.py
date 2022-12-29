"""Module for estimating turbulence

"""
import logging

import numpy as np
import xarray as xr

from .data_operator import GetRestructuredData

module_logger = logging.getLogger("lidarSuit.wind_prop_retrieval_6_beam")
module_logger.debug("loading wind_prop_retrieval_6_beam")


class SixBeamMethod:

    """6 beam method

    Implementation of the 6 beam method
    to retrieve the Reynolds stress tensor
    components based on the 6 Beam method
    developed by Sathe at all 2015.
    See: https://doi.org/10.5194/amt-8-729-2015

    Parameters
    ----------
    data : object
        an instance of the object generated by the
        lst.GetRestructuredData()

    freq : int
        number of profiles used to calculate
        the variance

    freq90 : int
        number of profiles used to calculate
        the variance

    Returns
    -------
    var_comp_ds : xarray.DataSet
        a dataset of the eynolds stress tensor
        matrix elementes

    """

    def __init__(self, data, freq=10, freq90=10):

        self.logger = logging.getLogger(
            "lidarSuit.wind_prop_retrieval_6_beam.SixBeamMethod"
        )
        self.logger.info("creating an instance of SixBeamMethod")

        if not isinstance(data, GetRestructuredData):
            self.logger.error(
                "wrong data type: expecting a instance of GetRestructuredData"
            )
            raise TypeError

        self.elv = data.data_transf.elv.values
        self.azm = data.data_transf.azm.values

        self.get_m_matrix()
        self.get_m_matrix_inv()
        self.radial_variances = {}
        self.calc_variances(data, freq, freq90)

        self.get_s_matrix()
        self.get_sigma()
        self.get_variance_ds()

    def get_m_matrix(self):

        """
        This method populates the coefficient matrix (M).
        Each element of M is one of the coefficients from
        equation 3 from Newman et. all 2016. The lines 0 to 4
        in M are the radial velocities coefficients from the
        non 90 deg elevation and different azimuths. Line 6
        in M has the coefficients from the radial velocity
        at 90 deg elevation.
        See: https://doi.org/10.5194/amt-9-1993-2016


        M x SIGMA = S
        """

        phis = np.append(np.ones_like(self.azm) * self.elv, np.array([90]))
        phis_rad = np.deg2rad(phis)

        thetas = np.append(self.azm, np.array([0]))
        thetas_rad = np.deg2rad(thetas)

        m_matrix = np.ones((len(phis), len(thetas))) * np.nan

        for i, theta in enumerate(thetas_rad):

            phi = phis_rad[i]

            ci1 = np.cos(phi) ** 2 * np.sin(theta) ** 2
            ci2 = np.cos(phi) ** 2 * np.cos(theta) ** 2

            ci3 = np.sin(phi) ** 2
            ci4 = np.cos(phi) ** 2 * np.cos(theta) * np.sin(theta)

            ci5 = np.cos(phi) * np.sin(phi) * np.sin(theta)
            ci6 = np.cos(phi) * np.sin(phi) * np.cos(theta)

            m_matrix_line = np.array(
                [ci1, ci2, ci3, ci4 * 2, ci5 * 2, ci6 * 2]
            )

            m_matrix[i] = m_matrix_line

        self.m_matrix = m_matrix

        return self

    def get_m_matrix_inv(self):

        """
        This method calculates the inverse matrix of M.
        """

        self.m_matrix_inv = np.linalg.inv(self.m_matrix)

        return self

    # new approach to calculate the variances ##############

    def calc_variances(self, data, freq, freq90):

        interp_data_transf = data.data_transf.interp(
            time=data.data_transf_90.time, method="nearest"
        )
        self.get_variance(interp_data_transf, freq=freq)
        self.get_variance(
            -1 * data.data_transf_90, freq=freq90, name="rVariance90"
        )  # think about the -1 coefficient

        return self

    def get_variance(self, data, freq=10, name="rVariance"):

        """
        This method calculates the variance from the
        observed radial velocities within a time window.
        The default size of this window is 10 minutes.

        Parameters
        ----------
        data : xarray.DataArray
            a dataarray of the slanted azimuthal observations

        freq : int
            number of profiles used to calculate
            the variance

        """

        variance = data.rolling(
            time=freq, center=True, min_periods=int(freq * 0.3)
        ).var()

        self.radial_variances[name] = variance

        return self

    # new approach to calculate the variances ##############

    def get_s_matrix(self):

        """
        This method fills the observation variance matrix (S).
        """

        s_matrix = np.dstack(
            (
                self.radial_variances["rVariance"].values,
                self.radial_variances["rVariance90"].values[
                    :, :, np.newaxis, np.newaxis
                ],
            )
        )

        self.s_matrix = s_matrix

    def get_sigma(self):

        """
        This method calculates the components of the
        Reynolds stress tensor (SIGMA).

        SIGMA = M^-1 x S
        """

        self.sigma_matrix = np.matmul(self.m_matrix_inv, self.s_matrix)

        return self

    def get_variance_ds(self):

        """
        This method converts the SIGMA into a xarray dataset.
        """

        var_comp_ds = xr.Dataset()
        var_comp_name = ["u", "v", "w", "uv", "uw", "vw"]

        for i, var_comp in enumerate(var_comp_name):

            tmp_data = xr.DataArray(
                self.sigma_matrix[:, :, i, 0],
                dims=("time", "range"),
                coords={
                    "time": self.radial_variances["rVariance90"].time,
                    "range": self.radial_variances["rVariance"].range,
                },
                name=f"var_{var_comp}",
            )

            var_comp_ds = xr.merge([var_comp_ds, tmp_data])

        self.var_comp_ds = var_comp_ds
