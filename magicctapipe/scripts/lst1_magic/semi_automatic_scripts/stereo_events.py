"""
This scripts generates and runs the bashscripts
to compute the stereo parameters of DL1 MC and
Coincident MAGIC+LST data files.

Usage:
$ python stereo_events.py (-c config.yaml)

If you want to compute the stereo parameters only the real data or only the MC data,
you can do as follows:

Only real data:
$ python stereo_events.py --analysis-type onlyReal (-c config.yaml)

Only MC:
$ python stereo_events.py --analysis-type onlyMC (-c config.yaml)
"""

import argparse
import glob
import logging
import os
from pathlib import Path

import joblib
import numpy as np
import yaml

from magicctapipe import __version__
from magicctapipe.io import resource_file
from magicctapipe.scripts.lst1_magic.semi_automatic_scripts.clusters import (
    rc_lines,
    slurm_lines,
)

__all__ = ["configfile_stereo", "bash_stereo", "bash_stereoMC"]

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def configfile_stereo(ids, target_dir, source_name, NSB_match):

    """
    This function creates the configuration file needed for the event stereo step

    Parameters
    ----------
    ids : list
        List of telescope IDs
    target_dir : str
        Path to the working directory
    source_name : str
        Name of the target source
    NSB_match : bool
        If real data are matched to pre-processed MCs or not
    """

    config_file = resource_file("config.yaml")
    with open(
        config_file, "rb"
    ) as fc:  # "rb" mode opens the file in binary format for reading
        config_dict = yaml.safe_load(fc)
    conf = {"stereo_reco": config_dict["stereo_reco"]}

    file_name = f"{target_dir}/v{__version__}/{source_name}/config_stereo.yaml"
    with open(file_name, "w") as f:
        lines = [
            "mc_tel_ids:",
            f"\n    LST-1: {ids[0]}",
            f"\n    LST-2: {ids[1]}",
            f"\n    LST-3: {ids[2]}",
            f"\n    LST-4: {ids[3]}",
            f"\n    MAGIC-I: {ids[4]}",
            f"\n    MAGIC-II: {ids[5]}",
            "\n",
        ]
        f.writelines(lines)
        yaml.dump(conf, f, default_flow_style=False)


def bash_stereo(target_dir, source, env_name, NSB_match):

    """
    This function generates the bashscript for running the stereo analysis.

    Parameters
    ----------
    target_dir : str
        Path to the working directory
    source : str
        Target name
    env_name : str
        Name of the environment
    NSB_match : bool
        If real data are matched to pre-processed MCs or not
    """

    process_name = source

    if NSB_match:
        coincidence_DL1_dir = f"{target_dir}/v{__version__}/{source}"
    else:
        coincidence_DL1_dir = f"{target_dir}/v{__version__}/{source}/DL1/Observations"

    listOfNightsLST = np.sort(glob.glob(f"{coincidence_DL1_dir}/DL1Coincident/*"))

    for nightLST in listOfNightsLST:
        stereoDir = f"{coincidence_DL1_dir}/DL1Stereo/{nightLST.split('/')[-1]}"
        os.makedirs(f"{stereoDir}/logs", exist_ok=True)
        if not os.listdir(f"{nightLST}"):
            continue
        if len(os.listdir(nightLST)) < 2:
            continue

        os.system(
            f"ls {nightLST}/*LST*.h5 >  {stereoDir}/logs/list_coin.txt"
        )  # generating a list with the DL1 coincident data files.
        with open(f"{stereoDir}/logs/list_coin.txt", "r") as f:
            process_size = len(f.readlines()) - 1

        if process_size < 0:
            continue

        slurm = slurm_lines(
            queue="short",
            job_name=f"{process_name}_stereo",
            array=process_size,
            out_name=f"{stereoDir}/logs/slurm-%x.%A_%a",
        )
        rc = rc_lines(
            store="$SAMPLE ${SLURM_ARRAY_JOB_ID} ${SLURM_ARRAY_TASK_ID}",
            out="$OUTPUTDIR/logs/list",
        )
        lines = (
            slurm
            + [
                f"export INPUTDIR={nightLST}\n",
                f"export OUTPUTDIR={stereoDir}\n",
                "SAMPLE_LIST=($(<$OUTPUTDIR/logs/list_coin.txt))\n",
                "SAMPLE=${SAMPLE_LIST[${SLURM_ARRAY_TASK_ID}]}\n",
                "export LOG=$OUTPUTDIR/logs/stereo_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.log\n",
                f"conda run -n {env_name} lst1_magic_stereo_reco --input-file $SAMPLE --output-dir $OUTPUTDIR --config-file {target_dir}/v{__version__}/{source}/config_stereo.yaml >$LOG 2>&1\n",
            ]
            + rc
        )
        with open(f"{source}_StereoEvents_{nightLST.split('/')[-1]}.sh", "w") as f:
            f.writelines(lines)


def bash_stereoMC(target_dir, identification, env_name, source):

    """
    This function generates the bashscript for running the stereo analysis.

    Parameters
    ----------
    target_dir : str
        Path to the working directory
    identification : str
        Particle name. Options: protons, gammadiffuse, gammas, protons_test
    env_name : str
        Name of the environment
    source : str
        Name of the target source
    """

    process_name = source

    inputdir = f"{target_dir}/{source}/DL1/MC/{identification}/Merged"
    os.makedirs(f"{inputdir}/StereoMerged", exist_ok=True)

    os.system(
        f"ls {inputdir}/dl1*.h5 >  {inputdir}/list_coin.txt"
    )  # generating a list with the DL1 coincident data files.
    with open(f"{inputdir}/list_coin.txt", "r") as f:
        process_size = len(f.readlines()) - 1

    with open(f"StereoEvents_MC_{identification}.sh", "w") as f:
        slurm = slurm_lines(
            queue="xxl",
            job_name=f"{process_name}_stereo",
            array=f"{process_size}%100",
            mem="8g",
            out_name=f"{inputdir}/StereoMerged/logs/slurm-%x.%A_%a",
        )
        lines = slurm + [
            f"export INPUTDIR={inputdir}\n",
            f"export OUTPUTDIR={inputdir}/StereoMerged\n",
            "SAMPLE_LIST=($(<$INPUTDIR/list_coin.txt))\n",
            "SAMPLE=${SAMPLE_LIST[${SLURM_ARRAY_TASK_ID}]}\n",
            "export LOG=$OUTPUTDIR/stereo_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.log\n",
            f"conda run -n {env_name} lst1_magic_stereo_reco --input-file $SAMPLE --output-dir $OUTPUTDIR --config-file {target_dir}/{source}/config_stereo.yaml >$LOG 2>&1",
        ]
        f.writelines(lines)


def main():

    """
    Here we read the config_general.yaml file and call the functions defined above.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config-file",
        "-c",
        dest="config_file",
        type=str,
        default="./config_general.yaml",
        help="Path to a configuration file",
    )

    parser.add_argument(
        "--analysis-type",
        "-t",
        choices=["onlyReal", "onlyMC"],
        dest="analysis_type",
        type=str,
        default="doEverything",
        help="You can type 'onlyReal' or 'onlyMC' to run this script only on real or MC data, respectively.",
    )

    args = parser.parse_args()
    with open(
        args.config_file, "rb"
    ) as f:  # "rb" mode opens the file in binary format for reading
        config = yaml.safe_load(f)

    target_dir = Path(config["directories"]["workspace_dir"])

    env_name = config["general"]["env_name"]

    NSB_match = config["general"]["NSB_matching"]
    telescope_ids = list(config["mc_tel_ids"].values())
    source_in = config["data_selection"]["source_name_database"]
    source = config["data_selection"]["source_name_output"]

    if source_in is None:
        source_list = joblib.load("list_sources.dat")
    else:
        source_list = [source]
    for source_name in source_list:

        print("***** Generating file config_stereo.yaml...")
        configfile_stereo(telescope_ids, target_dir, source_name, NSB_match)

        # Below we run the analysis on the MC data
        if (
            (args.analysis_type == "onlyMC")
            or (args.analysis_type == "doEverything")
            and not NSB_match
        ):
            print("***** Generating the bashscript for MCs...")
            for part in ["gammadiffuse", "gammas", "protons", "protons_test"]:
                bash_stereoMC(target_dir, part, env_name, source_name)

            list_of_stereo_scripts = np.sort(glob.glob("StereoEvents_MC_*.sh"))

            for n, run in enumerate(list_of_stereo_scripts):
                if n == 0:
                    launch_jobs = f"stereo{n}=$(sbatch --parsable {run})"
                else:
                    launch_jobs = f"{launch_jobs} && stereo{n}=$(sbatch --parsable {run})"

            os.system(launch_jobs)

        # Below we run the analysis on the real data

        print("***** Generating the bashscript...")
        bash_stereo(target_dir, source_name, env_name, NSB_match)

        print("***** Submitting processess to the cluster...")
        print(f"Process name: {source_name}_stereo")
        print(
            f"To check the jobs submitted to the cluster, type: squeue -n {source_name}_stereo"
        )

        # Below we run the bash scripts to find the stereo events
        list_of_stereo_scripts = np.sort(glob.glob(f"{source_name}_StereoEvents*.sh"))
        if len(list_of_stereo_scripts) < 1:
            continue
        for n, run in enumerate(list_of_stereo_scripts):
            if n == 0:
                launch_jobs = f"stereo{n}=$(sbatch --parsable {run})"
            else:
                launch_jobs = f"{launch_jobs} && stereo{n}=$(sbatch --parsable {run})"

        os.system(launch_jobs)


if __name__ == "__main__":
    main()
