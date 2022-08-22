#!/usr/bin/env python
# coding: utf-8

"""
This script processes DL1 events and reconstructs the stereo parameters
with more than one telescope information. The quality cuts specified in
the configuration file are applied to events before the reconstruction.

When the input is real data containing LST-1 and MAGIC events, it checks
if the angular distance of their pointing directions is lower than the
limit specified in the configuration file. This is in principle to avoid
the reconstruction of the data taken in too-mispointing conditions. For
example, DL1 data may contain coincident events taken with different
wobble offsets between the systems.

If the "--magic-only" argument is given, it reconstructs the stereo
parameters using only MAGIC events.

Usage:
$ python lst1_magic_stereo_reco.py
--input-file ./dl1_coincidence/dl1_LST-1_MAGIC.Run03265.0040.h5
--output-dir ./dl1_stereo
--config-file ./config.yaml
(--magic-only)
"""

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from astropy import units as u
from astropy.coordinates import Angle, angular_separation
from ctapipe.containers import (
    ArrayEventContainer,
    CameraHillasParametersContainer,
    ImageParametersContainer,
)
from ctapipe.instrument import SubarrayDescription
from ctapipe.reco import HillasReconstructor
from magicctapipe.utils import (
    calculate_impact,
    calculate_mean_direction,
    get_stereo_events,
    save_pandas_to_table,
)

__all__ = ["calculate_pointing_separation", "stereo_reconstruction"]

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def calculate_pointing_separation(event_data):
    """
    Calculates the angular distance of the LST-1 and MAGIC pointing
    directions.

    The input data is supposed to have the index
    (obs_id, event_id, tel_id).

    Parameters
    ----------
    event_data: pandas.core.frame.DataFrame
        Pandas data frame of LST-1 and MAGIC events

    Returns
    -------
    theta: astropy.units.quantity.Quantity
        Angular distance of the LST-1 and MAGIC pointing directions
    """

    df_lst = event_data.query("tel_id == 1")

    obs_ids = df_lst.index.get_level_values("obs_id").tolist()
    event_ids = df_lst.index.get_level_values("event_id").tolist()

    multi_indices = pd.MultiIndex.from_arrays(
        [obs_ids, event_ids], names=["obs_id", "event_id"]
    )

    df_magic = event_data.query("tel_id == [2, 3]")
    df_magic.reset_index(level="tel_id", inplace=True)
    df_magic = df_magic.loc[multi_indices]

    # Calculate the mean of the M1 and M2 pointing directions:
    pointing_az_magic, pointing_alt_magic = calculate_mean_direction(
        lon=df_magic["pointing_az"], lat=df_magic["pointing_alt"]
    )

    theta = angular_separation(
        lon1=u.Quantity(df_lst["pointing_az"].to_numpy(), u.rad),
        lat1=u.Quantity(df_lst["pointing_alt"].to_numpy(), u.rad),
        lon2=u.Quantity(pointing_az_magic.to_numpy(), u.rad),
        lat2=u.Quantity(pointing_alt_magic.to_numpy(), u.rad),
    )

    return theta


def stereo_reconstruction(input_file, output_dir, config, magic_only_analysis=False):
    """
    Processes DL1 events and reconstructs the stereo parameters with
    more than one telescope information.

    Parameters
    ----------
    input_file: str
        Path to an input DL1 data file
    output_dir: str
        Path to a directory where to save an output DL1-stereo data file
    config: dict
        Configuration for the LST-1 + MAGIC analysis
    magic_only_analysis: bool
        If `True`, it reconstructs the stereo parameters using only
        MAGIC events
    """

    # Load the input file:
    logger.info(f"\nInput file:\n{input_file}")

    event_data = pd.read_hdf(input_file, key="events/parameters")
    event_data.set_index(["obs_id", "event_id", "tel_id"], inplace=True)
    event_data.sort_index(inplace=True)

    is_simulation = "true_energy" in event_data.columns
    logger.info(f"\nIs simulation: {is_simulation}")

    if magic_only_analysis:
        logger.info("\nSelecting only MAGIC events...")
        event_data.query("tel_id > 1", inplace=True)

    config_stereo = config["stereo_reco"]
    event_data = get_stereo_events(event_data, config_stereo["quality_cuts"])

    subarray = SubarrayDescription.from_hdf(input_file)
    tel_positions = subarray.positions

    logger.info("\nSubarray configuration:")
    for tel_id in subarray.tel.keys():
        logger.info(
            f"\tTelescope {tel_id}: {subarray.tel[tel_id].name}, "
            f"position = {tel_positions[tel_id].round(2)}"
        )

    # Check the angular distance of the LST-1 and MAGIC pointing directions:
    tel_ids = np.unique(event_data.index.get_level_values("tel_id")).tolist()

    if not is_simulation and (tel_ids != [2, 3]):

        logger.info(
            "\nChecking the angular distance of the "
            "LST-1 and MAGIC pointing directions..."
        )

        theta = calculate_pointing_separation(event_data)
        theta_max = np.max(theta.to(u.arcmin))
        theta_uplim = config_stereo["theta_uplim"] * u.arcmin

        if theta_max > theta_uplim:
            logger.info(
                f"--> The maximum angular distance is {theta_max:.3f}, "
                f"which is larger than the limit {theta_uplim}. Exiting."
            )
            sys.exit()

        else:
            logger.info(
                f"--> The maximum angular distance is {theta_max:.3f}, "
                f"which is smaller than the limit {theta_uplim}."
            )

    # Configure the HillasReconstructor:
    hillas_reconstructor = HillasReconstructor(subarray)

    # Since the reconstructor requires the ArrayEventContainer, here we
    # initialize it and reset necessary information event-by-event:
    event = ArrayEventContainer()

    # Start processing the events:
    logger.info("\nReconstructing the stereo parameters...")

    pointing_az_mean, pointing_alt_mean = calculate_mean_direction(
        lon=event_data["pointing_az"], lat=event_data["pointing_alt"]
    )

    group_size = event_data.groupby(["obs_id", "event_id"]).size()
    obs_ids = group_size.index.get_level_values("obs_id")
    event_ids = group_size.index.get_level_values("event_id")

    for i_evt, (obs_id, event_id) in enumerate(zip(obs_ids, event_ids)):

        if i_evt % 100 == 0:
            logger.info(f"{i_evt} events")

        event.pointing.array_altitude = pointing_alt_mean.iloc[i_evt] * u.rad
        event.pointing.array_azimuth = pointing_az_mean.iloc[i_evt] * u.rad

        df_evt = event_data.query(f"(obs_id == {obs_id}) & (event_id == {event_id})")

        tel_ids = df_evt.index.get_level_values("tel_id")

        for tel_id in tel_ids:

            df_tel = df_evt.query(f"tel_id == {tel_id}")

            event.pointing.tel[tel_id].altitude = df_tel["pointing_alt"].iloc[0] * u.rad
            event.pointing.tel[tel_id].azimuth = df_tel["pointing_az"].iloc[0] * u.rad

            hillas_params = CameraHillasParametersContainer(
                intensity=float(df_tel["intensity"].iloc[0]),
                x=u.Quantity(df_tel["x"].iloc[0], u.m),
                y=u.Quantity(df_tel["y"].iloc[0], u.m),
                r=u.Quantity(df_tel["r"].iloc[0], u.m),
                phi=Angle(df_tel["phi"].iloc[0], u.deg),
                length=u.Quantity(df_tel["length"].iloc[0], u.m),
                width=u.Quantity(df_tel["width"].iloc[0], u.m),
                psi=Angle(df_tel["psi"].iloc[0], u.deg),
                skewness=float(df_tel["skewness"].iloc[0]),
                kurtosis=float(df_tel["kurtosis"].iloc[0]),
            )

            event.dl1.tel[tel_id].parameters = ImageParametersContainer(
                hillas=hillas_params
            )

        # Reconstruct the stereo parameters:
        hillas_reconstructor(event)

        stereo_params = event.dl2.stereo.geometry["HillasReconstructor"]
        stereo_params.az.wrap_at(360 * u.deg, inplace=True)  # wrap at 0 <= az < 360 deg

        for tel_id in tel_ids:

            # Calculate the impact parameter:
            impact = calculate_impact(
                shower_alt=stereo_params.alt,
                shower_az=stereo_params.az,
                core_x=stereo_params.core_x,
                core_y=stereo_params.core_y,
                tel_pos_x=tel_positions[tel_id][0],
                tel_pos_y=tel_positions[tel_id][1],
                tel_pos_z=tel_positions[tel_id][2],
            )

            # Set the stereo parameters to the data frame:
            params = {
                "alt": stereo_params.alt.to_value(u.deg),
                "alt_uncert": stereo_params.alt_uncert.to_value(u.deg),
                "az": stereo_params.az.to_value(u.deg),
                "az_uncert": stereo_params.az_uncert.to_value(u.deg),
                "core_x": stereo_params.core_x.to_value(u.m),
                "core_y": stereo_params.core_y.to_value(u.m),
                "core_uncert": stereo_params.core_uncert.to_value(u.m),
                "impact": impact.to_value(u.m),
                "h_max": stereo_params.h_max.to_value(u.m),
                "h_max_uncert": stereo_params.h_max_uncert.to_value(u.m),
                "is_valid": int(stereo_params.is_valid),
            }

            event_data.loc[(obs_id, event_id, tel_id), params.keys()] = params.values()

    n_events_processed = i_evt + 1
    logger.info(f"{n_events_processed} events")

    # Save the data in an output file:
    Path(output_dir).mkdir(exist_ok=True, parents=True)

    input_file_name = Path(input_file).name

    if magic_only_analysis:
        output_file_name = input_file_name.replace("dl1", "dl1_stereo_magic_only")
    else:
        output_file_name = input_file_name.replace("dl1", "dl1_stereo")

    output_file = f"{output_dir}/{output_file_name}"

    event_data.reset_index(inplace=True)

    save_pandas_to_table(
        event_data, output_file, group_name="/events", table_name="parameters"
    )

    subarray.to_hdf(output_file)

    if is_simulation:
        sim_config = pd.read_hdf(input_file, key="simulation/config")

        save_pandas_to_table(
            sim_config,
            output_file,
            group_name="/simulation",
            table_name="config",
            mode="a",
        )

    logger.info(f"\nOutput file:\n{output_file}")


def main():

    start_time = time.time()

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input-file",
        "-i",
        dest="input_file",
        type=str,
        required=True,
        help="Path to an input DL1 data file.",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        dest="output_dir",
        type=str,
        default="./data",
        help="Path to a directory where to save an output DL1-stereo data file.",
    )

    parser.add_argument(
        "--config-file",
        "-c",
        dest="config_file",
        type=str,
        default="./config.yaml",
        help="Path to a yaml configuration file.",
    )

    parser.add_argument(
        "--magic-only",
        dest="magic_only",
        action="store_true",
        help="Reconstruct the stereo parameters using only MAGIC events.",
    )

    args = parser.parse_args()

    with open(args.config_file, "rb") as f:
        config = yaml.safe_load(f)

    # Process the input data:
    stereo_reconstruction(args.input_file, args.output_dir, config, args.magic_only)

    logger.info("\nDone.")

    process_time = time.time() - start_time
    logger.info(f"\nProcess time: {process_time:.0f} [sec]\n")


if __name__ == "__main__":
    main()
