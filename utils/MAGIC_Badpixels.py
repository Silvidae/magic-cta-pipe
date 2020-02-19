import numpy as np
from ctapipe.instrument import CameraGeometry

class MAGICBadPixelsCalc():

    def __init__(self, camera=None, config=None, tool=None, **kwargs):

        # MAGIC telescope description
        if camera == None:
            camera = CameraGeometry.from_name('MAGICCam')

        self.n_camera_pixels = camera.n_pixels

        self.config = config
        self.is_update = np.zeros(2, dtype=np.bool) + 1

        if 'pedestalLevel' in config:
            self.pedestalLevel = config['pedestalLevel']
        else:
            self.pedestalLevel = 400.

        if 'pedestalLevelVariance' in config:
            self.pedestalLevelVariance = config['pedestalLevelVariance']
        else:
            self.pedestalLevelVariance = 4.5
            
        if 'pedestalType' in config:
            pedestalTypeName = config['pedestalType']
            if pedestalTypeName == 'Fundamental':
                self.pedestalType = 0
            elif pedestalTypeName == 'FromExtractor':
                self.pedestalType = 1
            elif pedestalTypeName == 'FromExtractorRndm':
                self.pedestalType = 2
            else:
                raise ValueError("pedestalType must be chosen from 'Fundamental', 'FromExtractor', or 'FromExtractorRndm'")
        else:
            self.pedestalType = 2
        
        self.sample_times = [None, None]
        self.n_samples = [None, None]
        #self.pedestal_info = pedestal_info
        
    def _check_pedestal_rms(self, charge_std):

        if (len(charge_std)) != self.n_camera_pixels:
            print(len(charge_std))
            print(self.n_camera_pixels)
            raise ValueError("charge_std must be an array of length equal to number of MAGIC camera pixels")

        meanrms = 0.
        for i in range(self.n_camera_pixels):

            if (charge_std[i] <= 0 or charge_std[i] >= 200 * self._getpixratiosqrt(i)):
                continue

            #const Byte_t aidx = (*fGeomCam)[i].GetAidx();
            meanrms += charge_std[i];
        
        # if no pixel has a minimum signal, return
        if meanrms == 0:
            return False;

        meanrms /= self.n_camera_pixels
        
        meanrms2 = 0.
        varrms2 = 0.
        
        npix = 0
        for i in range(self.n_camera_pixels):

            # Calculate the corrected means:
    
            if (charge_std[i] <= 0.5 * meanrms or charge_std[i] >= 1.5 * meanrms):
                continue
    
            meanrms2 += charge_std[i]
            varrms2 += charge_std[i]**2
            npix += 1

        # if no pixel has a minimum signal, return
        lolim1 = 0
        lolim2 = 0  # Precalcualtion of limits
        uplim1 = 0
        uplim2 = 0  # for speeed reasons

        if npix == 0 or meanrms2 == 0:
            return False

        meanrms2 /= npix

        if self.pedestalLevel > 0:
            lolim1 = meanrms2 / self.pedestalLevel
            uplim1 = meanrms2 * self.pedestalLevel

        if self.pedestalLevelVariance > 0:
            varrms2 /= npix
            varrms2 = np.sqrt(varrms2 - meanrms2 * meanrms2 )

            lolim2 = meanrms2 - self.pedestalLevelVariance * varrms2
            uplim2  = meanrms2 + self.pedestalLevelVariance * varrms2

        bads = 0
    
        # Blind the Bad Pixels
        for i in range(self.n_camera_pixels):    
            if ((self.pedestalLevel <= 0          or (charge_std[i] > lolim1 and charge_std[i] <= uplim1)) 
                and (self.pedestalLevelVariance <= 0 or (charge_std[i] > lolim2 and charge_std[i] <= uplim2))):
                continue
    
            self.hotpixels[i] = True
            bads += 1

        return True;

    def _getpixratiosqrt(self, i_pix):
#         i_pixzero = np.where(self.geom.pix_id == 0)[0][0]
#         return np.sqrt(self.geom.pix_area[i_pix] / self.geom.pix_area[i_pixzero])
        return 1.
    
    def get_hotpixels(self, event):
        hotpixel_mask = [[None],[None]]
        event_time = event.trig.gps_time.unix
        
        
        for tel_id in event.mon.tels_with_data:
            
            try:
                self.sample_times[tel_id - 1][0]
            except:
                self.n_samples[tel_id - 1] = len(event.mon.tel[tel_id].pedestal.sample_time)
                self.sample_times[tel_id - 1] = np.zeros(self.n_samples[tel_id - 1])
                for i in range(self.n_samples[tel_id - 1]):
                    self.sample_times[tel_id - 1][i] = event.mon.tel[tel_id].pedestal.sample_time[i].unix
             
            if event.mon.tel[tel_id].pedestal.charge_std_outliers == None or self.is_update[tel_id - 1] == True:
                # calculate only once the hot pixel array of the monitoring data:
                print("Update hot pixels for M%d" % tel_id)
                event.mon.tel[tel_id].pedestal.charge_std_outliers = []
                for i_sample in range(self.n_samples[tel_id - 1]):
                    self.hotpixels = np.zeros(self.n_camera_pixels, dtype=np.bool)
                    charge_std = event.mon.tel[tel_id].pedestal.charge_std[self.pedestalType][i_sample]
                    self._check_pedestal_rms(charge_std)
                    event.mon.tel[tel_id].pedestal.charge_std_outliers.append(self.hotpixels)
                self.is_update[tel_id - 1] = False
                    
            # now interpolate for the event time:
            times_diff = abs(event_time - self.sample_times[tel_id - 1])
            min_diff = min(times_diff)
            i_min = np.where(times_diff == min_diff)[0][0]
            hotpixel_mask[tel_id - 1] = event.mon.tel[tel_id].pedestal.charge_std_outliers[i_min]

        return hotpixel_mask
