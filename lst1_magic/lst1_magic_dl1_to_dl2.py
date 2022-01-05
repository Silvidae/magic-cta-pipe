#!/usr/bin/env python
# coding: utf-8

"""
Author: Yoshiki Ohtani (ICRR, ohtani@icrr.u-tokyo.ac.jp) 

Reconstruct the DL2 parameters (i.e., energy, direction and gammaness) with trained RFs.
The RFs will be applied per telescope combination and per telescope type.
If real data is input, the parameters in the Alt/Az coordinate will be transformed to the RA/Dec coordinate. 

Usage:
$ python lst1_magic_dl1_to_dl2.py 
--input-file "./data/dl1_stereo/dl1_stereo_lst1_magic_Run02923.0040.h5"
--output-file "./data/dl2/dl2_lst1_magic_Run02923.0040.h5"
--energy-rfs "./data/rfs/energy_rfs_*.joblib"
--direction-rfs "./data/rfs/direction_rfs_*.joblib"
--classifier-rfs "./data/rfs/classifier_rfs_*.joblib"
"""

import glob
import time
import yaml
import argparse
import warnings
import pandas as pd
from astropy import units as u
from astropy.time import Time

from utils import (
    EnergyEstimatorPandas,
    DirectionEstimatorPandas,
    EventClassifierPandas,
    transform_to_radec
)

warnings.simplefilter('ignore')

__all__ = ['dl1_to_dl2']


def dl1_to_dl2(input_file, output_file, energy_rfs=None, direction_rfs=None, classifier_rfs=None):

    print(f'\nLoading the input data file:\n{input_file}')

    data_joint = pd.read_hdf(input_file, key='events/params')
    data_joint.sort_index(inplace=True)

    data_type = 'mc' if ('mc_energy' in data_joint.columns) else 'real'

    if energy_rfs != None:

        # --- reconstruct energy ---
        print('\nLoading the following energy RFs:')
        
        file_paths = glob.glob(energy_rfs)
        file_paths.sort()

        reco_params = pd.DataFrame()

        for path in file_paths:

            print(path)

            energy_estimator = EnergyEstimatorPandas()
            energy_estimator.load(path)

            tel_ids = list(energy_estimator.telescope_rfs.keys())
            
            df = data_joint.query(f'(tel_id == {tel_ids}) & (multiplicity == {len(tel_ids)})')
            df.dropna(subset=energy_estimator.feature_names, inplace=True)

            df['multiplicity'] = df.groupby(['obs_id', 'event_id']).size()
            df.query(f'multiplicity == {len(tel_ids)}', inplace=True)

            n_events = len(df.groupby(['obs_id', 'event_id']).size())

            if n_events > 0:
                print(f'--> {n_events} events are found. Applying...\n')
                df_reco_energy = energy_estimator.predict(df)
                reco_params = reco_params.append(df_reco_energy)

            else:
                print('--> No corresponding events are found. Skipping.\n')
                continue

        reco_params.sort_index(inplace=True)
        data_joint = data_joint.join(reco_params)

        del energy_estimator
    
    if direction_rfs != None:

        # --- reconstruct arrival direction ---
        print('\nLoading the following direction RFs:')

        file_paths = glob.glob(direction_rfs)
        file_paths.sort()

        reco_params = pd.DataFrame()

        for path in file_paths:

            print(path)

            direction_estimator = DirectionEstimatorPandas()
            direction_estimator.load(path)

            tel_ids = list(direction_estimator.telescope_rfs.keys())
            
            df = data_joint.query(f'(tel_id == {tel_ids}) & (multiplicity == {len(tel_ids)})')
            df.dropna(subset=direction_estimator.feature_names, inplace=True)

            df['multiplicity'] = df.groupby(['obs_id', 'event_id']).size()
            df.query(f'multiplicity == {len(tel_ids)}', inplace=True)

            n_events = len(df.groupby(['obs_id', 'event_id']).size())

            if n_events > 0:
                print(f'--> {n_events} events are found. Applying...\n')
                df_reco_direction = direction_estimator.predict(df)
                reco_params = reco_params.append(df_reco_direction)

            else:
                print('--> No corresponding events are found. Skipping.\n')
                continue

        reco_params.sort_index(inplace=True)
        data_joint = data_joint.join(reco_params)

        if data_type == 'real':

            # --- transform Alt/Az to RA/Dec coordinate ---
            print('Transforming Alt/Az to RA/Dec coordinate...\n')

            timestamps = Time(data_joint['timestamp'].values, format='unix', scale='utc')

            ra_tel, dec_tel = transform_to_radec(
                alt=u.Quantity(data_joint['alt_tel'].values, u.deg),
                az=u.Quantity(data_joint['az_tel'].values, u.deg),
                timestamp=timestamps
            )

            ra_tel_mean, dec_tel_mean = transform_to_radec(
                alt=u.Quantity(data_joint['alt_tel_mean'].values, u.deg),
                az=u.Quantity(data_joint['az_tel_mean'].values, u.deg),
                timestamp=timestamps
            )
            
            reco_ra, reco_dec = transform_to_radec(
                alt=u.Quantity(data_joint['reco_alt'].values, u.deg), 
                az=u.Quantity(data_joint['reco_az'].values, u.deg),
                timestamp=timestamps
            ) 

            reco_ra_mean, reco_dec_mean = transform_to_radec(
                alt=u.Quantity(data_joint['reco_alt_mean'].values, u.deg), 
                az=u.Quantity(data_joint['reco_az_mean'].values, u.deg),
                timestamp=timestamps
            ) 

            data_joint['ra_tel'] = ra_tel.to(u.deg).value
            data_joint['dec_tel'] = dec_tel.to(u.deg).value
            data_joint['ra_tel_mean'] = ra_tel_mean.to(u.deg).value
            data_joint['dec_tel_mean'] = dec_tel_mean.to(u.deg).value
            data_joint['reco_ra'] = reco_ra.to(u.deg).value
            data_joint['reco_dec'] = reco_dec.to(u.deg).value  
            data_joint['reco_ra_mean'] = reco_ra_mean.to(u.deg).value
            data_joint['reco_dec_mean'] = reco_dec_mean.to(u.deg).value

        del direction_estimator

    if classifier_rfs != None:

        # --- reconstruct gammaness ---
        print('\nLoading the following classifier RFs:')
        
        file_paths = glob.glob(classifier_rfs)
        file_paths.sort()

        reco_params = pd.DataFrame()

        for path in file_paths:

            print(path)

            event_classifier = EventClassifierPandas()
            event_classifier.load(path)

            tel_ids = event_classifier.telescope_rfs.keys()

            df = data_joint.query(f'(tel_id == {list(tel_ids)}) & (multiplicity == {len(tel_ids)})')
            df.dropna(subset=event_classifier.feature_names, inplace=True)

            df['multiplicity'] = df.groupby(['obs_id', 'event_id']).size()
            df.query(f'multiplicity == {len(tel_ids)}', inplace=True)

            n_events = len(df.groupby(['obs_id', 'event_id']).size())

            if n_events > 0:
                print(f'--> {n_events} events are found. Applying...\n')
                df_reco_class = event_classifier.predict(df)
                reco_params = reco_params.append(df_reco_class)

            else:
                print('--> No corresponding events are found. Skipping.\n')
                continue

        reco_params.sort_index(inplace=True)
        data_joint = data_joint.join(reco_params)

        del event_classifier

    # --- save the data frame ---
    data_joint.to_hdf(output_file, key='events/params')

    print(f'\nOutput data file: {output_file}')


def main():

    start_time = time.time()

    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--input-file', '-i', dest='input_file', type=str, 
        help='Path to an input DL1-stereo data file.'
    )

    parser.add_argument(
        '--output-file', '-o', dest='output_file', type=str, default='./dl2.h5',
        help='Path to an output DL2 data file.'
    )

    parser.add_argument(
        '--energy-rfs', '-e', dest='energy_rfs', type=str, default=None,
        help='Path to trained energy RFs.'
    )

    parser.add_argument(
        '--direction-rfs', '-d', dest='direction_rfs', type=str, default=None,
        help='Path to trained direction RFs.'
    )

    parser.add_argument(
        '--classifier-rfs', '-c', dest='classifier_rfs', type=str, default=None,
        help='Path to trained classifier RFs.'
    )

    args = parser.parse_args()

    dl1_to_dl2(args.input_file, args.output_file, args.energy_rfs, args.direction_rfs, args.classifier_rfs)

    print('\nDone.')

    end_time = time.time()
    print(f'\nProcess time: {end_time - start_time:.0f} [sec]\n')


if __name__ == '__main__':
    main()
