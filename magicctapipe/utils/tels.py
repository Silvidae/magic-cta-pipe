import os
import sys
import datetime
import pandas as pd
from astropy import units as u

import ctapipe
from ctapipe.instrument import CameraGeometry
from ctapipe.instrument import TelescopeDescription
from ctapipe.instrument import OpticsDescription
from ctapipe.instrument import SubarrayDescription


def get_tel_descriptions(name, cam, tel_ids):
    """Get telescope descriptions from ctapipe, for the same telescope type.
    Returns a dictionary with repeated description of the selected telescope, 
    depending on the number of telescopes in the telescope array under study

    Parameters
    ----------
    name : str
        telescope name
    cam : str
        camera name
    tel_ids : list
        telescope ids

    Returns
    -------
    dict
        tel_descriptions
    """
    optics = OpticsDescription.from_name(name)
    cam = CameraGeometry.from_name(cam)
    tel_description = TelescopeDescription(
        name=name,
        tel_type=name,
        optics=optics,
        camera=cam
    )
    tel_descriptions = {}
    for tel_id in tel_ids:
        tel_descriptions = {
            **tel_descriptions,
            **{tel_id: tel_description}
        }
    return tel_descriptions


def get_array_tel_descriptions(tel_ids_LST, tel_ids_MAGIC):
    """Get telescope descriptions for the array

    Parameters
    ----------
    tel_ids_LST : list
        list of selected LST tel_ids
    tel_ids_MAGIC : list
        list of selected MAGIC tel_ids

    Returns
    -------
    dict
        array_tel_descriptions
    """
    array_tel_descriptions = {}
    if(len(tel_ids_LST) > 0):
        array_tel_descriptions = {
            **array_tel_descriptions,
            **get_tel_descriptions(
                name='LST', cam='LSTCam', tel_ids=tel_ids_LST)
        }
    if(len(tel_ids_MAGIC) > 0):
        array_tel_descriptions = {
            **array_tel_descriptions,
            **get_tel_descriptions(
                name='MAGIC', cam='MAGICCam', tel_ids=tel_ids_MAGIC)
        }
    return array_tel_descriptions


def get_tel_ids_dl1(df):
    """Return telescope ids from loaded dl1 pandas dataframe

    Parameters
    ----------
    df : pandas.DataFrame
        pandas dataframe

    Returns
    -------
    list
        telescope ids
    """
    return list(df.index.levels[2])


def convert_positions_dict(positions_dict):
    """Convert telescope positions loaded from config.yaml file from 
    adimensional numbers to u.m (astropy units)

    Parameters
    ----------
    positions_dict : dict
        telescopes positions

    Returns
    -------
    dict
        telescopes positions
    """
    for k_ in positions_dict.keys():
        positions_dict[k_] *= u.m
    return positions_dict


def intersec_tel_ids(all_tel_ids_LST=[1, 2, 3, 4], all_tel_ids_MAGIC=[5, 6],
                     tel_ids_sel=[1, 2, 3, 4, 5, 6]):
    """Get telescope ids from the intersection between the selected ids and the
    telescope ids of the telescope array

    Parameters
    ----------
    all_tel_ids_LST : list, optional
        LST ids, by default [1, 2, 3, 4]
    all_tel_ids_MAGIC : list, optional
        MAGIC ids, by default [5, 6]
    tel_ids_sel : list, optional
        telescope ids selected for the analysis, by default [1, 2, 3, 4, 5, 6]

    Returns
    -------
    tuple
        - tel_ids: intersection with tel_ids_sel and the sum between 
            all_tel_ids_LST and all_tel_ids_MAGIC
        - tel_ids_LST: LST telescope ids in tel_ids
        - tel_ids_MAGIC: MAGIC telescope ids in tel_ids
    """
    sum_ids = all_tel_ids_LST + all_tel_ids_MAGIC
    tel_ids = list(set(tel_ids_sel).intersection(sum_ids))
    tel_ids_LST = list(set(tel_ids).intersection(all_tel_ids_LST))
    tel_ids_MAGIC = list(set(tel_ids).intersection(all_tel_ids_MAGIC))
    print("Selected tels:", tel_ids)
    print("LST tels:", tel_ids_LST)
    print("MAGIC tels:", tel_ids_MAGIC)
    return tel_ids, tel_ids_LST, tel_ids_MAGIC


def get_tel_name(tel_id, cfg):
    """Get tel name from tel_id

    Parameters
    ----------
    tel_id : int
        telescope id
    cfg : dict
        configuration dictionary loaded from config file

    Returns
    -------
    str
        telescope name
    """
    for i, tel_label in enumerate(cfg['all_tels']['tel_n']):
        if(tel_id in cfg[tel_label]['tel_ids']):
            n = cfg['all_tels']['tel_n_short'][i]
            j = tel_id-cfg[tel_label]['tel_ids'][0]+1
            name = f'{n}{j}'
            break
    return name
