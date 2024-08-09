#!/usr/bin/env python
# coding: utf-8

"""
This script corrects LST-1 and Magic data for the cloud affection.

Usage:
$ python lst_m1_m2_cloud_correction.py
--input-file dl1_stereo/dl1_LST-1_MAGIC.Run03265.0040.h5
(--output-dir dl1_corrected)
"""
import argparse
import logging

import astropy.units as u
import numpy as np
import pandas as pd
from astropy.coordinates import AltAz, SkyCoord
from ctapipe.coordinates import TelescopeFrame
from ctapipe.image import (
    concentration_parameters,
    hillas_parameters,
    leakage_parameters,
    timing_parameters,
)
from ctapipe.instrument import SubarrayDescription
from ctapipe.io import read_table
from lstchain.reco import disp
from lstchain.reco.utils import sky_to_camera

from magicctapipe.io import save_pandas_data_in_table

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def model0(imp, h, zd):
    """
    Calculates distance

    Parameters
    ----------
    imp : float
        Impact
    h : float
        Height
    zd : float
        Zenith distance

    Returns
    -------
    float
        Angular distance in units of degree
    """
    d = h / np.cos(zd)
    return np.arctan((imp / d).to("")).to_value("deg")


def model2(imp, h, zd):
    """
    Calculates model

    Parameters
    ----------
    imp : float
        Impact
    h : float
        Height
    zd : float
        Zenith distance

    Returns
    -------
    float
        Bias
    """
    H0 = 2.2e3 * u.m
    bias = 0.877 + 0.015 * ((h + H0) / (7.0e3 * u.m))
    return bias * model0(imp, h, zd)


def trans_height(x, Hc, dHc, trans):
    """
    Calculates height of a cloud transmission

    Parameters
    ----------
    x : float
        Position of each layer
    Hc : float
        Cloud above the telescope
    dHc : float
        Cloud thickness
    trans : float
        Transmission of the cloud

    Returns
    -------
    float
        Height of a cloud transmission
    """
    t = pow(trans, ((x - Hc) / dHc).to_value(""))
    t = np.where(x < Hc, 1, t)
    t = np.where(x > Hc + dHc, trans, t)
    return t


def process_telescope_data(
    input_file, output_file, telid, focal_eq, focal_eff, camgeom
):
    """
    Corrects LST-1 and MAGIC data affected by a cloud presence

    Parameters
    ----------
    input_file : str
        Path to an input .h5 DL1 file
    output_file : str
        Path to a directory where to save an output corrected DL1 data file
    telid : int
        LST-1 and MAGIC telescope ids
    focal_eq : float
        Equivalent focal length
    focal_eff : float
        Effective focal length
    camgeom : int
        Camera geometry

    Returns
    -------
    pandas.core.frame.DataFrame
        Data frame of corrected DL1 parameters
    """

    Hc = 5900 * u.m  # cloud above the telescope
    dHc = 1320 * u.m

    data = []
    dl1_params = read_table(input_file, "/events/parameters")
    dl1_images = read_table(input_file, "/events/dl1/image_" + str(telid))

    inds = np.where(
        np.logical_and(dl1_params["intensity"] > 0.0, dl1_params["tel_id"] == telid)
    )[0]
    for index in inds:
        event_id_lst = dl1_params["event_id_lst"][index]
        obs_id_lst = dl1_params["obs_id_lst"][index]
        event_id = dl1_params["event_id_magic"][index]
        obs_id = dl1_params["obs_id_magic"][index]
        if telid == 1:
            event_id, obs_id = event_id_lst, obs_id_lst

        pointing_az = dl1_params["pointing_az"][index]
        pointing_alt = dl1_params["pointing_alt"][index]
        pointing_alt_deg = np.rad2deg(pointing_alt)
        zenith = 90.0 - pointing_alt_deg

        x = dl1_params["x"][index] * u.m
        y = dl1_params["y"][index] * u.m
        psi = dl1_params["psi"][index] * u.deg
        wl = dl1_params["width"][index] / dl1_params["length"][index]
        log_intensity = np.log10(dl1_params["intensity"])[index]
        timestamp = dl1_params["timestamp"][index]
        time_diff = dl1_params["time_diff"][index]
        n_islands = dl1_params["n_islands"][index]
        n_pixels = dl1_params["n_pixels"][index]
        signal_pixels = dl1_params["n_pixels"][index]

        trans = 0.58  # transmission of the cloud
        trans = trans ** (1 / np.cos(zenith))  # .to_value("")
        nlayers = 10  # to simplify calculations with the model cloud is considered to be composed of multiple layers
        Hcl = np.linspace(Hc, Hc + dHc, nlayers)  # position of each layer
        transl = trans_height(Hcl, Hc, dHc, trans)  # transmissions of each layer
        transl = np.append(
            transl, transl[-1]
        )  # add one more element for heights above the top of the cloud

        alt_rad = np.deg2rad(dl1_params["alt"][index])
        az_rad = np.deg2rad(dl1_params["az"][index])

        eff_focal = focal_eq[telid]  # *u.m # m
        m2deg = np.rad2deg(1) / eff_focal * u.degree

        impact = dl1_params["impact"][index] * u.m
        psi = dl1_params["psi"][index] * u.deg
        cog_x = (dl1_params["x"][index] * m2deg).value * u.deg
        cog_y = (dl1_params["y"][index] * m2deg).value * u.deg

        # Source position
        pointing_altaz = SkyCoord(alt=alt_rad * u.rad, az=az_rad * u.rad, frame=AltAz())
        telescope_pointing = SkyCoord(
            alt=dl1_params["pointing_alt"][index] * u.rad,
            az=dl1_params["pointing_az"][index] * u.rad,
            frame=AltAz(),
        )

        tel_frame = TelescopeFrame(telescope_pointing=telescope_pointing)
        tel = pointing_altaz.transform_to(tel_frame)

        src_x = tel.fov_lat.to(u.deg).value
        src_y = tel.fov_lon.to(u.deg).value

        # Transform to Engineering camera
        src_x, src_y = -src_y * u.deg, -src_x * u.deg
        cog_x, cog_y = -cog_y, -cog_x

        pix_x_tel = (camgeom[telid].pix_x * m2deg).to(u.deg)
        pix_y_tel = (camgeom[telid].pix_y * m2deg).to(u.deg)

        distance = np.abs(
            (pix_y_tel - src_y) * np.cos(psi) + (pix_x_tel - src_x) * np.sin(psi)
        )

        d2_cog_src = (cog_x - src_x) ** 2 + (cog_y - src_y) ** 2
        d2_cog_pix = (cog_x - pix_x_tel) ** 2 + (cog_y - pix_y_tel) ** 2
        d2_src_pix = (src_x - pix_x_tel) ** 2 + (src_y - pix_y_tel) ** 2

        distance[d2_cog_pix > d2_cog_src + d2_src_pix] = 0
        dist_corr_layer = model2(impact, Hcl, zenith) * u.deg

        ilayer = np.digitize(distance, dist_corr_layer)
        trans_pixels = transl[ilayer]

        inds_img = np.where(
            (dl1_images["event_id"] == event_id)
            & (dl1_images["tel_id"] == telid)
            & (dl1_images["obs_id"] == obs_id)
        )[0]

        if len(inds_img) > 0:
            for index_img in inds_img:
                image = dl1_images["image"][index_img]
                cleanmask = dl1_images["image_mask"][index_img]
                peak_time = dl1_images["peak_time"][index_img]
                image /= trans_pixels
                corr_image = image.copy()
                corr_image[~cleanmask] = 0

                hillas_params = hillas_parameters(camgeom[telid], corr_image)
                timing_params = timing_parameters(
                    camgeom[telid], corr_image, peak_time, hillas_params
                )
                leakage_params = leakage_parameters(
                    camgeom[telid], corr_image, signal_pixels
                )
                conc_params = concentration_parameters(
                    camgeom[telid], corr_image, hillas_params
                )

                source_pos_in_camera = sky_to_camera(
                    alt_rad * u.rad,
                    az_rad * u.rad,
                    focal_eq[telid],
                    pointing_alt * u.rad,
                    pointing_az * u.rad,
                )

                disp_parameters = disp.disp(
                    x, y, source_pos_in_camera.x, source_pos_in_camera.y, psi
                )

                disp_dx = disp_parameters[0].value * u.m
                disp_dy = disp_parameters[1].value * u.m
                disp_norm = disp_parameters[telid].value * u.m
                disp_angle = disp_parameters[3].value * u.rad
                disp_sign = disp_parameters[4]
                src_x = source_pos_in_camera.x.value * u.m
                src_y = source_pos_in_camera.y.value * u.m

                data.append(
                    {
                        "tel_id": telid,
                        "event_id": event_id,
                        "obs_id": obs_id,
                        "event_id_lst": event_id_lst,
                        "obs_id_lst": obs_id_lst,
                        "intensity": hillas_params.intensity,
                        "x": hillas_params.x,
                        "y": hillas_params.y,
                        "r": hillas_params.r,
                        "phi": hillas_params.phi,
                        "length": hillas_params.length,
                        "width": hillas_params.width,
                        "psi": hillas_params.psi,
                        "skewness": hillas_params.skewness,
                        "kurtosis": hillas_params.kurtosis,
                        "slope": timing_params.slope,
                        "intercept": timing_params.intercept,
                        "deviation": timing_params.deviation,
                        "intensity_width_1": leakage_params.intensity_width_1,
                        "intensity_width_2": leakage_params.intensity_width_2,
                        "pixels_width_1": leakage_params.pixels_width_1,
                        "pixels_width_2": leakage_params.pixels_width_2,
                        "conc_cog": conc_params.cog,
                        "conc_core": conc_params.core,
                        "conc_pixel": conc_params.pixel,
                        "impact": impact,
                        "pointing_alt": pointing_alt,
                        "pointing_az": pointing_az,
                        "alt": alt_rad,
                        "az": az_rad,
                        "wl": wl,
                        "log_intensity": log_intensity,
                        "timestamp": timestamp,
                        "time_diff": time_diff,
                        "zenith": zenith,
                        "n_islands": n_islands,
                        "n_pixels": n_pixels,
                        "disp_dx": disp_dx,
                        "disp_dy": disp_dy,
                        "disp_norm": disp_norm,
                        "disp_angle": disp_angle,
                        "disp_sign": disp_sign,
                        "src_x": src_x,
                        "src_y": src_y,
                    }
                )

    df = pd.DataFrame(data)
    return df


def main():
    """Main function."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input_file",
        "-i",
        dest="input_file",
        type=str,
        required=True,
        help="Path to an input .h5 DL1 data file",
    )

    parser.add_argument(
        "--output_file",
        "-o",
        dest="output_file",
        type=str,
        default="./data",
        help="Path to a directory where to save an output corrected DL1 file",
    )

    args = parser.parse_args()

    subarray_info = SubarrayDescription.from_hdf(args.input_file)

    tel_descriptions = subarray_info.tel
    camgeom = {}
    for telid, telescope in tel_descriptions.items():
        camgeom[telid] = telescope.camera.geometry  # 1 - LSTCam; 2,3 - MAGICCam

    optics_table = read_table(
        args.input_file, "/configuration/instrument/telescope/optics"
    )
    focal_eq = {}
    focal_eff = {}

    for telid, telescope in tel_descriptions.items():
        optics_row = optics_table[optics_table["optics_name"] == telescope.name]
        if len(optics_row) > 0:
            focal_length_eq = optics_row["equivalent_focal_length"][0]
            focal_eq[telid] = focal_length_eq * u.m
            focal_length_eff = optics_row["effective_focal_length"][0]
            focal_eff[telid] = focal_length_eff * u.m

    df_lst = process_telescope_data(
        args.input_file, args.output_file, 1, focal_eq, focal_eff, camgeom
    )
    df_m1 = process_telescope_data(
        args.input_file, args.output_file, 2, focal_eq, focal_eff, camgeom
    )
    df_m2 = process_telescope_data(
        args.input_file, args.output_file, 3, focal_eq, focal_eff, camgeom
    )

    df_all = pd.concat([df_lst, df_m1, df_m2], ignore_index=True)

    columns_to_convert = [
        "x",
        "y",
        "r",
        "phi",
        "length",
        "width",
        "psi",
        "slope",
        "impact",
        "disp_dx",
        "disp_dy",
        "disp_norm",
        "disp_angle",
        "src_x",
        "src_y",
    ]

    for col in columns_to_convert:
        df_all[col] = df_all[col].apply(
            lambda x: x.value if isinstance(x, u.Quantity) else x
        )

    for col in columns_to_convert:
        df_all[col] = pd.to_numeric(df_all[col], errors="coerce")

    save_pandas_data_in_table(
        df_all, args.output_file, group_name="/events", table_name="parameters"
    )

    subarray_info.to_hdf(args.output_file)

    logger.info("\nDone.")


if __name__ == "__main__":
    main()
