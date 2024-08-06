"""
This script splits the proton MC data sample into "train"
and "test" and generates the bash scripts to merge MC and
real data files by calling the script "merge_hdf_files.py":

MAGIC:

Merge the subruns into runs for M1 and M2 individually.

MC:

Merges all MC runs in a node

Usage:
$ merging_runs (-c config.yaml) (-t analysis_type)

If you want to merge only the MAGIC or only the MC data,
you can do as follows:

Only MAGIC:
$ merging_runs -t onlyMAGIC (-c config.yaml)

Only MC:
$ merging_runs -t onlyMC (-c config.yaml)
"""

import argparse
import glob
import logging
import os
from pathlib import Path

import joblib
import numpy as np
import yaml
from tqdm import tqdm

from magicctapipe import __version__
from magicctapipe.scripts.lst1_magic.semi_automatic_scripts.clusters import (
    rc_lines,
    slurm_lines,
)

__all__ = ["split_train_test", "merge", "mergeMC"]

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def split_train_test(target_dir, train_fraction):

    """
    This function splits the MC proton sample in 2, i.e. the "test" and the "train" subsamples, in case you want to make performance studies on MC. For regular analyses, you can/should use the whole MC sample for training.
    It generates 2 subdirectories in the directory .../DL1/MC/protons named "test" and "train" and creates sub-sub-directories with the names of all nodes.
    For each node sub-sub-directory we move `train_fraction` of the .h5 files to the "train" subdirectory and `1-train_fraction` of the .h5 files to the "test" subdirectory.

    Parameters
    ----------
    target_dir : str
        Path to the working directory
    train_fraction : float
        Fraction of proton MC files to be used in the training of RFs
    """

    proton_dir = f"{target_dir}/v{__version__}/MC/DL1/protons"

    list_of_dir = np.sort(glob.glob(f"{proton_dir}/node*{os.path.sep}"))

    for directory in tqdm(
        range(len(list_of_dir))
    ):  # tqdm allows us to print a progessbar in the terminal
        node = list_of_dir[directory].split("/")[-2]
        os.makedirs(f"{proton_dir}/train/{node}", exist_ok=True)
        os.makedirs(
            f'{proton_dir}/../protons_test/{list_of_dir[directory].split("/")[-2]}',
            exist_ok=True,
        )
        list_of_runs = np.sort(
            glob.glob(f'{proton_dir}/{list_of_dir[directory].split("/")[-2]}/*.h5')
        )
        number_train_runs = int(len(list_of_runs) * train_fraction)
        for j in list_of_runs[0:number_train_runs]:
            os.system(f"mv {j} {proton_dir}/train/{node}")

        os.system(f"cp {list_of_dir[directory]}logs/*.txt {proton_dir}/train/{node}")
        os.system(
            f"mv {list_of_dir[directory]}logs/*.txt {proton_dir}/../protons_test/{node}"
        )
        os.system(
            f"mv {list_of_dir[directory]}*.h5 {proton_dir}/../protons_test/{node}"
        )
        os.system(f"rm -r {list_of_dir[directory]}")


def merge(target_dir, MAGIC_runs, env_name, source, cluster):

    """
    This function creates the bash scripts to run merge_hdf_files.py for real data

    Parameters
    ----------
    target_dir : str
        Path to the working directory
    MAGIC_runs : matrix of strings
        Matrix of [(date,run)] n-tuples
    env_name : str
        Name of the environment
    source : str
        Target name
    cluster : str
        Cluster system
    """

    process_name = f"merging_{source}"

    MAGIC_DL1_dir = f"{target_dir}/v{__version__}/{source}/DL1/"

    if cluster != "SLURM":
        logger.warning(
            "Automatic processing not implemented for the cluster indicated in the config file"
        )
        return
    lines = slurm_lines(
        queue="short",
        job_name=process_name,
        mem="2g",
        out_name=f"{MAGIC_DL1_dir}/Merged/logs/slurm-%x.%j",
    )
    os.makedirs(f"{MAGIC_DL1_dir}/Merged/logs", exist_ok=True)

    with open(f"{source}_Merge_MAGIC.sh", "w") as f:
        f.writelines(lines)
        for magic in [1, 2]:
            for i in MAGIC_runs:
                # Here is a difference w.r.t. original code. If only one telescope data are available they will be merged now for this telescope
                indir = f"{MAGIC_DL1_dir}/M{magic}/{i[0]}/{i[1]}"
                if os.path.exists(f"{indir}"):
                    outdir = f"{MAGIC_DL1_dir}/Merged/{i[0]}"
                    os.makedirs(f"{outdir}/logs", exist_ok=True)

                    f.write(
                        f"conda run -n {env_name} merge_hdf_files --input-dir {indir} --output-dir {outdir} >{outdir}/logs/merge_M{magic}_{i[0]}_{i[1]}_${{SLURM_JOB_ID}}.log 2>&1\n"
                    )
                    rc = rc_lines(
                        store=f"{indir} ${{SLURM_JOB_ID}}",
                        out=f"{outdir}/logs/list",
                    )
                    f.writelines(rc)
                    os.system(f"echo {indir} >> {outdir}/logs/list_dl0.txt")
                else:
                    logger.error(f"{indir} does not exist")


def mergeMC(target_dir, identification, env_name, cluster):

    """
    This function creates the bash scripts to run merge_hdf_files.py in all MC nodes.

    Parameters
    ----------
    target_dir : str
        Path to the working directory
    identification : str
        Tells which sample to process
    env_name : str
        Name of the environment
    cluster : str
        Cluster system
    """

    process_name = "merging_MC"

    MC_DL1_dir = f"{target_dir}/v{__version__}/MC/DL1"
    os.makedirs(f"{MC_DL1_dir}/{identification}/Merged/logs", exist_ok=True)

    if identification == "protons":
        list_of_nodes = np.sort(glob.glob(f"{MC_DL1_dir}/{identification}/train/node*"))
    else:
        list_of_nodes = np.sort(glob.glob(f"{MC_DL1_dir}/{identification}/node*"))

    np.savetxt(
        f"{MC_DL1_dir}/{identification}/list_of_nodes.txt", list_of_nodes, fmt="%s"
    )

    process_size = len(list_of_nodes) - 1

    if cluster != "SLURM":
        logger.warning(
            "Automatic processing not implemented for the cluster indicated in the config file"
        )
        return
    with open(f"Merge_MC_{identification}.sh", "w") as f:
        slurm = slurm_lines(
            queue="short",
            array=process_size,
            mem="7g",
            job_name=process_name,
            out_name=f"{MC_DL1_dir}/{identification}/Merged/logs/slurm-%x.%A_%a",
        )
        lines_bash_file = slurm + [
            f"SAMPLE_LIST=($(<{MC_DL1_dir}/{identification}/list_of_nodes.txt))\n",
            "SAMPLE=${SAMPLE_LIST[${SLURM_ARRAY_TASK_ID}]}\n",
            f"export LOG={MC_DL1_dir}/{identification}/Merged/logs"
            + "/merged_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}.log\n",
            f"conda run -n {env_name} merge_hdf_files --input-dir $SAMPLE --output-dir {MC_DL1_dir}/{identification}/Merged >$LOG 2>&1\n",
        ]
        f.writelines(lines_bash_file)


def main():

    """
    Main function
    """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config-file",
        "-c",
        dest="config_file",
        type=str,
        default="./config_auto_MCP.yaml",
        help="Path to a configuration file",
    )

    parser.add_argument(
        "--analysis-type",
        "-t",
        choices=["onlyMAGIC", "onlyMC"],
        dest="analysis_type",
        type=str,
        default="doEverything",
        help="You can type 'onlyMAGIC' or 'onlyMC' to run this script only on MAGIC or MC data, respectively.",
    )

    args = parser.parse_args()
    with open(
        args.config_file, "rb"
    ) as f:  # "rb" mode opens the file in binary format for reading
        config = yaml.safe_load(f)

    target_dir = Path(config["directories"]["workspace_dir"])

    NSB_match = config["general"]["NSB_matching"]
    train_fraction = float(config["general"]["proton_train_fraction"])

    env_name = config["general"]["env_name"]
    source_in = config["data_selection"]["source_name_database"]
    source = config["data_selection"]["source_name_output"]
    cluster = config["general"]["cluster"]

    source_list = []
    if source_in is None:
        source_list = joblib.load("list_sources.dat")

    else:
        source_list.append(source)
    if not NSB_match:
        if (args.analysis_type == "onlyMC") or (args.analysis_type == "doEverything"):
            # Here we slice the proton MC data into "train" and "test" (but first we check if the directory already exists):
            if not os.path.exists(f"{target_dir}/v{__version__}/MC/DL1/protons_test"):
                print("***** Splitting protons into 'train' and 'test' datasets...")
                split_train_test(target_dir, train_fraction)

            print("***** Generating merge_MC bashscripts...")
            mergeMC(
                target_dir, "protons", env_name, cluster
            )  # generating the bash script to merge the files
            mergeMC(
                target_dir, "gammadiffuse", env_name, cluster
            )  # generating the bash script to merge the files
            mergeMC(
                target_dir, "gammas", env_name, cluster
            )  # generating the bash script to merge the files
            mergeMC(target_dir, "protons_test", env_name, cluster)
            mergeMC(target_dir, "helium", env_name, cluster)
            mergeMC(target_dir, "electrons", env_name, cluster)

            print("***** Running merge_hdf_files.py on the MC data files...")

            # Below we run the bash scripts to merge the MC files
            list_of_merging_scripts = np.sort(glob.glob("Merge_MC_*.sh"))
            if len(list_of_merging_scripts) < 1:
                logger.warning("No bash script has been produced for MC")
            # TODO: check

            else:
                launch_jobs = ""
                for n, run in enumerate(list_of_merging_scripts):
                    launch_jobs += (" && " if n > 0 else "") + f"sbatch {run}"

                os.system(launch_jobs)

    for source_name in source_list:
        MAGIC_runs_and_dates = f"{source_name}_MAGIC_runs.txt"
        MAGIC_runs = np.genfromtxt(
            MAGIC_runs_and_dates, dtype=str, delimiter=",", ndmin=2
        )

        # Below we run the analysis on the MAGIC data
        if (
            (args.analysis_type == "onlyMAGIC")
            or (args.analysis_type == "doEverything")
            or (NSB_match)
        ):
            print("***** Generating merge_MAGIC bashscripts...")
            merge(
                target_dir,
                MAGIC_runs,
                env_name,
                source_name,
                cluster,
            )  # generating the bash script to merge the subruns

            print("***** Running merge_hdf_files.py on the MAGIC data files...")

            # Below we run the bash scripts to merge the MAGIC files
            list_of_merging_scripts = np.sort(
                glob.glob(f"{source_name}_Merge_MAGIC*.sh")
            )
            if len(list_of_merging_scripts) < 1:
                logger.warning("No bash scripts for real data")
                continue
            launch_jobs = ""
            for n, run in enumerate(list_of_merging_scripts):
                launch_jobs += (" && " if n > 0 else "") + f"sbatch {run}"

            os.system(launch_jobs)

        print(f"Process name: merging_{source_name}")
        print(
            f"To check the jobs submitted to the cluster, type: squeue -n merging_{source_name}"
        )


if __name__ == "__main__":
    main()
