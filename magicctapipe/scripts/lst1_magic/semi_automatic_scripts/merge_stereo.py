import argparse
import glob
import logging
import os
from pathlib import Path
import joblib
import numpy as np
import yaml
from magicctapipe import __version__
from magicctapipe.scripts.lst1_magic.semi_automatic_scripts.clusters import (
    rc_lines,
    slurm_lines,
)

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.INFO)


def MergeStereo(target_dir, env_name, source, NSB_match, cluster):
    """
    This function creates the bash scripts to run merge_hdf_files.py in all DL2 subruns.

    Parameters
    ----------
    target_dir: str
        Path to the working directory
    """

    process_name = source
    if NSB_match:
        stereo_DL1_dir = f"{target_dir}/v{__version__}/{source}"
    else:
        stereo_DL1_dir = f"{target_dir}/v{__version__}/{source}/DL1/Observations"

    listOfNightsLST = np.sort(glob.glob(f"{stereo_DL1_dir}/DL1Stereo/*"))
    if cluster == 'SLURM':
        for nightLST in listOfNightsLST:
            stereoMergeDir = (
                f"{stereo_DL1_dir}/DL1Stereo/{nightLST.split('/')[-1]}/Merged"
            )
            os.makedirs(f"{stereoMergeDir}/logs", exist_ok=True)
            if not os.listdir(f"{nightLST}"):
                continue
            if len(os.listdir(nightLST)) < 3:
                continue

        
            slurm = slurm_lines(
                queue="short",
                job_name=f"{process_name}_stereo_merge",
                out_name=f"{stereoMergeDir}/logs/slurm-%x.%A_%a",
            )
            rc = rc_lines(
                store=f"{nightLST} ${{SLURM_JOB_ID}}", out=f"{stereoMergeDir}/logs/list"
            )
            os.system(f"echo {nightLST} >> {stereoMergeDir}/logs/list_dl0.txt") 
            lines = (
                slurm
                + [
                    f"conda run -n {env_name} merge_hdf_files --input-dir {nightLST} --output-dir {stereoMergeDir} --run-wise >{stereoMergeDir}/logs/merge_{nightLST.split('/')[-1]}_${{SLURM_JOB_ID}}.log\n"
                ]
                + rc
            )
            
            with open(f"{source}_StereoMerge_{nightLST.split('/')[-1]}.sh", "w") as f:
                f.writelines(lines)
    else:
        logger.warning('Automatic processing not implemented for the cluster indicated in the config file')
        return

               
  
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

    args = parser.parse_args()
    with open(
        args.config_file, "rb"
    ) as f:  # "rb" mode opens the file in binary format for reading
        config = yaml.safe_load(f)

    target_dir = Path(config["directories"]["workspace_dir"])

    NSB_match = config["general"]["NSB_matching"]
    env_name = config["general"]["env_name"]

    
    source_in = config["data_selection"]["source_name_database"]
    source = config["data_selection"]["source_name_output"]
    cluster = config["general"]["cluster"]

    
    source_list = []
    if source_in is None:
        source_list = joblib.load("list_sources.dat")

    else:
        source_list.append(source)
    for source_name in source_list:
    
        print("***** Merging DL2 files run-wise...")
        MergeStereo(target_dir, env_name, source, NSB_match, cluster)

        list_of_merge = glob.glob(f"{source_name}_StereoMerge_*.sh")
        if len(list_of_merge) < 1:
            print(
                "Warning: no bash script has been produced"
            )
            continue

        launch_jobs = ""
        for n, run in enumerate(list_of_merge):
            launch_jobs = f"{launch_jobs} && RES{n}=$(sbatch --parsable {run})"

        os.system(launch_jobs)



if __name__ == "__main__":
    main()
