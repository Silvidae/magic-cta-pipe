#!/usr/bin/env python
# coding: utf-8

"""
Author: Yoshiki Ohtani (ICRR, ohtani@icrr.u-tokyo.ac.jp) 

Process the simtel MC DL0 data (simtel.gz) containing LST-1 or MAGIC events.
The allowed telescopes specified in the configuration file will only be processed.
The telescope IDs are reset to the following values when saving to an output file:
LST-1: tel_id = 1,  MAGIC-I: tel_id = 2,  MAGIC-II: tel_id = 3

Usage:
$ python lst1_magic_mc_dl0_to_dl1.py 
--input-file "./data/dl0/gamma_40deg_90deg_run1___cta-prod5-lapalma_LST-1_MAGIC_desert-2158m_mono_off0.4.simtel.gz"
--output-file "./data/dl1/dl1_lst1_magic_gamma_40deg_90deg_off0.4_run1.h5"
--config-file "./config.yaml"
"""

import time
import yaml
import argparse
import warnings
import numpy as np
from astropy import units as u
from astropy.coordinates import AltAz, SkyCoord
from astropy.coordinates.angle_utilities import angular_separation
from traitlets.config import Config
from ctapipe.io import event_source
from ctapipe.io import HDF5TableWriter
from ctapipe.calib import CameraCalibrator
from ctapipe.image import ImageExtractor, hillas_parameters, leakage
from ctapipe.image.cleaning import tailcuts_clean
from ctapipe.image.morphology import number_of_islands
from ctapipe.coordinates import CameraFrame, TelescopeFrame
from ctapipe.core.container import Container, Field

from utils import (
    add_noise_in_pixels,
    smear_light_in_pixels,
    apply_time_delta_cleaning,
    apply_dynamic_cleaning,
    MAGIC_Cleaning, 
    timing_parameters, 
    calc_impact
)

warnings.simplefilter('ignore')

__all__ = ['mc_dl0_to_dl1']


class EventInfoContainer(Container):

    obs_id = Field(-1, 'Observation ID')
    event_id = Field(-1, 'Event ID')
    tel_id = Field(-1, 'Telescope ID')
    alt_tel = Field(-1, 'Telescope altitude', u.deg)
    az_tel = Field(-1, 'Telescope azimuth', u.deg)
    mc_energy = Field(-1, 'MC event energy', u.TeV)
    mc_alt = Field(-1, 'MC event altitude', u.deg)
    mc_az = Field(-1, 'MC event azimuth', u.deg)
    mc_disp = Field(-1, 'MC event disp', u.deg)
    mc_core_x = Field(-1, 'MC core x', u.m)
    mc_core_y = Field(-1, 'MC core y', u.m)
    mc_impact = Field(-1, 'MC impact', u.m)
    n_islands = Field(-1, 'Number of image islands')
    n_pixels = Field(-1, 'Number of pixels of cleaned images')
    magic_stereo = Field(-1, 'True if M1 and M2 are triggered')


class SimInfoContainer(Container):

    nshow = Field(-1, 'Number of showers simulated')
    nscat = Field(-1, 'Numbers of uses of each shower')
    eslope = Field(-1, 'Power-law spectral index of spectrum')
    emin = Field(-1, 'Lower limit of energy range of primary particle', u.TeV)
    emax = Field(-1, 'Upper limit of energy range of primary particle', u.TeV)
    cscat = Field(-1, 'Maximum scatter range', u.m)
    viewcone = Field(-1, 'Maximum viewcone radius', u.deg)


def mc_dl0_to_dl1(input_file, output_file, config):

    print(f'\nInput data file:\n{input_file}')

    source = event_source(input_file)
    subarray = source.subarray

    print(f'\nSubarray configuration:\n{subarray.tels}')

    allowed_tel_ids = config['mc_allowed_tels']

    print(f'\nAllowed telescopes:\n{allowed_tel_ids}')

    process_lst1 = ('LST-1' in allowed_tel_ids.keys())
    process_m1 = ('MAGIC-I' in allowed_tel_ids.keys())
    process_m2 = ('MAGIC-II' in allowed_tel_ids.keys())

    if process_lst1:

        config_lst = config['LST']

        print(f'\nConfiguration for LST data process:\n{config_lst}')

        increase_nsb = config_lst['increase_nsb'].pop('use')
        increase_psf = config_lst['increase_psf'].pop('use')

        if increase_nsb:
            rng = np.random.default_rng(source.obs_id)

        use_only_main_island = config_lst['tailcuts_clean'].pop('use_only_main_island')
        use_time_delta_cleaning = config_lst['time_delta_cleaning'].pop('use')
        use_dynamic_cleaning = config_lst['dynamic_cleaning'].pop('use')

        extractor_name_lst = config_lst['image_extractor'].pop('name')

        extractor_lst = ImageExtractor.from_name(
            extractor_name_lst, subarray=subarray, config=Config(config_lst['image_extractor'])
        )

        calibrator_lst = CameraCalibrator(subarray, image_extractor=extractor_lst)

    if process_m1 or process_m2:

        config_magic = config['MAGIC']
        config_magic['magic_clean']['findhotpixels'] = False   # False for MC data, True for real data

        print(f'\nConfiguration for MAGIC data process:\n{config_magic}')

        extractor_name_magic = config_magic['image_extractor'].pop('name')

        extractor_magic = ImageExtractor.from_name(
            extractor_name_magic, subarray=subarray, config=Config(config_magic['image_extractor'])
        )

        calibrator_magic = CameraCalibrator(subarray, image_extractor=extractor_magic)

    # --- process the events ---
    with HDF5TableWriter(filename=output_file, group_name='events', overwrite=True) as writer:
        
        for tel_name, tel_id in zip(allowed_tel_ids.keys(), allowed_tel_ids.values()):

            print(f'\nProcessing the {tel_name} events...')

            source = event_source(input_file, allowed_tels=[tel_id])
            camera_geom = source.subarray.tel[tel_id].camera.geometry
            tel_positions = source.subarray.positions[tel_id]

            camera_frame = CameraFrame(
                focal_length=source.subarray.tel[tel_id].optics.equivalent_focal_length, 
                rotation=camera_geom.cam_rotation
            )

            if np.any(tel_name == np.array(['MAGIC-I', 'MAGIC-II'])):
                magic_clean = MAGIC_Cleaning.magic_clean(camera_geom, config_magic['magic_clean'])
            
            magic_stereo = None
            n_events_skipped = 0

            for i_ev, event in enumerate(source):

                if i_ev%100 == 0:
                    print(f'{i_ev} events')

                if process_m1 and process_m2:

                    trigger_m1 = (allowed_tel_ids['MAGIC-I'] in event.trigger.tels_with_trigger)
                    trigger_m2 = (allowed_tel_ids['MAGIC-II'] in event.trigger.tels_with_trigger)
                
                    magic_stereo = (trigger_m1 & trigger_m2)

                if tel_name == 'LST-1':

                    # --- calibration ---
                    calibrator_lst(event)

                    image = event.dl1.tel[tel_id].image
                    peak_time = event.dl1.tel[tel_id].peak_time

                    # --- image modification ---
                    if increase_nsb:
                        image = add_noise_in_pixels(rng, image, **config_lst['increase_nsb'])

                    if increase_psf:
                        image = smear_light_in_pixels(image, camera_geom, **config_lst['increase_psf'])

                    # --- image cleaning ---
                    signal_pixels = tailcuts_clean(camera_geom, image, **config_lst['tailcuts_clean'])

                    if use_only_main_island:

                        _, island_labels = number_of_islands(camera_geom, signal_pixels)
                        n_pixels_on_island = np.bincount(island_labels)
                        n_pixels_on_island[0] = 0  
                        max_island_label = np.argmax(n_pixels_on_island)
                        signal_pixels[island_labels != max_island_label] = False

                    if use_time_delta_cleaning:

                        signal_pixels = apply_time_delta_cleaning(
                            camera_geom, signal_pixels, peak_time, **config_lst['time_delta_cleaning']
                        )

                    if use_dynamic_cleaning:
                        signal_pixels = apply_dynamic_cleaning(image, signal_pixels, **config_lst['dynamic_cleaning'])

                elif np.any(tel_name == np.array(['MAGIC-I', 'MAGIC-II'])):

                    # --- calibration --- 
                    calibrator_magic(event)
                    
                    # --- image cleaning ---
                    signal_pixels, image, peak_time = magic_clean.clean_image(
                        event.dl1.tel[tel_id].image, event.dl1.tel[tel_id].peak_time
                    )

                n_islands, _ = number_of_islands(camera_geom, signal_pixels)
                n_pixels = np.count_nonzero(signal_pixels)

                image_cleaned = image.copy()
                image_cleaned[~signal_pixels] = 0

                peak_time_cleaned = peak_time.copy()
                peak_time_cleaned[~signal_pixels] = 0

                if np.all(image_cleaned == 0): 

                    print(f'--> {i_ev} event (event ID: {event.index.event_id}): ' \
                          'Could not survive the image cleaning. Skipping.')

                    n_events_skipped += 1
                    continue

                # --- hillas parameters calculation ---
                try:    
                    hillas_params = hillas_parameters(camera_geom, image_cleaned)
                
                except:

                    print(f'--> {i_ev} event (event ID: {event.index.event_id}): ' \
                          'Hillas parameters calculation failed. Skipping.')

                    n_events_skipped += 1
                    continue
                    
                # --- timing parameters calculation ---
                try:    
                    timing_params = timing_parameters(
                        camera_geom, image_cleaned, peak_time_cleaned, hillas_params, signal_pixels
                    )
                
                except:

                    print(f'--> {i_ev} event (event ID: {event.index.event_id}): ' \
                          'Timing parameters calculation failed. Skipping.')

                    n_events_skipped += 1
                    continue
                
                # --- leakage parameters calculation --- 
                try:
                    leakage_params = leakage(camera_geom, image, signal_pixels)
                
                except: 

                    print(f'--> {i_ev} event (event ID: {event.index.event_id}): ' \
                          'Leakage parameters calculation failed. Skipping.')

                    n_events_skipped += 1
                    continue

                # --- calculate additional parameters ---
                tel_pointing = AltAz(alt=event.pointing.tel[tel_id].altitude, az=event.pointing.tel[tel_id].azimuth)
                telescope_frame = TelescopeFrame(telescope_pointing=tel_pointing)

                event_coord = SkyCoord(hillas_params.x, hillas_params.y, frame=camera_frame)
                event_coord = event_coord.transform_to(telescope_frame)

                mc_disp = angular_separation(
                    lon1=event_coord.altaz.az, lat1=event_coord.altaz.alt, lon2=event.mc.az, lat2=event.mc.alt
                )

                mc_impact = calc_impact(
                    event.mc.core_x, event.mc.core_y, event.mc.az, event.mc.alt,
                    tel_positions[0], tel_positions[1], tel_positions[2]
                )

                # --- save the event information ---
                event_info = EventInfoContainer(
                    obs_id=event.index.obs_id,
                    event_id=event.index.event_id,
                    alt_tel=event.pointing.tel[tel_id].altitude,
                    az_tel=event.pointing.tel[tel_id].azimuth,
                    mc_energy=event.mc.energy,
                    mc_alt=event.mc.alt,
                    mc_az=event.mc.az,
                    mc_disp=mc_disp,
                    mc_core_x=event.mc.core_x,
                    mc_core_y=event.mc.core_y,
                    mc_impact=mc_impact,
                    n_islands=n_islands,
                    n_pixels=n_pixels,
                    magic_stereo=magic_stereo
                )

                if tel_name == 'LST-1':
                    event_info.tel_id = 1

                elif tel_name == 'MAGIC-I':
                    event_info.tel_id = 2
                
                elif tel_name == 'MAGIC-II':
                    event_info.tel_id = 3

                writer.write('params', (event_info, hillas_params, timing_params, leakage_params))

            print(f'{i_ev+1} events processed.')
            print(f'({n_events_skipped} events are skipped)')

    # --- save the simulation parameters ---
    with HDF5TableWriter(filename=output_file, group_name='simulation', mode='a') as writer:

        sim_info = SimInfoContainer(
            nshow=event.mcheader.num_showers,
            nscat=event.mcheader.shower_reuse,
            eslope=event.mcheader.spectral_index,
            emin=event.mcheader.energy_range_min,
            emax=event.mcheader.energy_range_max,
            cscat=event.mcheader.max_scatter_range,
            viewcone=event.mcheader.max_viewcone_radius
        )

        writer.write('params', sim_info)


def main():

    start_time = time.time()

    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--input-file', '-i', dest='input_file', type=str, 
        help='Path to an input simtel DL0 data file (*.simtel.gz).'
    )

    parser.add_argument(
        '--output-file', '-o', dest='output_file', type=str, default='./dl1_mc.h5',
        help='Path to an output DL1 data file.'
    )

    parser.add_argument(
        '--config-file', '-c', dest='config_file', type=str, default='./config.yaml',
        help='Path to a configuration file.'
    )

    args = parser.parse_args()

    config = yaml.safe_load(open(args.config_file, 'r'))

    mc_dl0_to_dl1(args.input_file, args.output_file, config)

    print('\nDone.')

    end_time = time.time()
    print(f'\nProcess time: {end_time - start_time:.0f} [sec]\n')


if __name__ == '__main__':
    main()
