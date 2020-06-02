from pyvisa import VisaIOError, ResourceManager
import re
import time
import numpy as np

from pylabnet.utils.logging.logger import LogHandler
import matplotlib.pyplot as plt

# Available input channels
CHANNEL_LIST = np.array([f'CH{i}' for i in range(1, 5)])

# Available trigger channels
TRIGGER_SOURCE_LIST = np.append(CHANNEL_LIST, ['EXT', 'EXT5', 'LINE'])


class Driver():

    def reset(self):
        """ Create factory reset"""
        self.device.write('FAC;WAIT')
        self.log.info("Reset to factory settings successfull.")

    def __init__(self, gpib_address, logger):
        """Instantiate driver class

        :gpib_address: GPIB-address of the scope, e.g. 'GPIB0::12::INSTR'
            Can be read out by using
                rm = pyvisa.ResourceManager()
                rm.list_resources()
        :logger: And instance of a LogClient
        """

        # Instantiate log
        self.log = LogHandler(logger=logger)

        self.rm = ResourceManager()

        try:
            self.device = self.rm.open_resource(gpib_address)
            device_id = self.device.query('*IDN?')
            self.log.info(f"Successfully connected to {device_id}.")
        except VisaIOError:
            self.log.error(f"Connection to {gpib_address} failed.")

        # reset to factory settings

        # We set a more forgiving timeout of 10s (default: 2s).
        self.device.timeout = 10000

        self.reset()

        # Add waittimes to make sure instrument is ready.
        time.sleep(5)

    def get_trigger_source(self):
        """ Return Trigger source"""

        # Query trigger source.
        res = self.device.query('TRIG:MAI:EDGE:SOU?')

        # Tidy up response using regex
        trig_channel = re.compile(
             ':TRIGGER:MAIN:EDGE:SOURCE[ ]([^\\n]+)'
            ).match(res).group(1)

        return trig_channel

    def set_trigger_source(self, trigger_source):
        """ Set trigger source"""

        if trigger_source not in TRIGGER_SOURCE_LIST:
            self.log.error(
                f"'{trigger_source}' no found, available trigger sources are {TRIGGER_SOURCE_LIST}.'"
            )

        # Set trigger source
        self.device.write(f'TRIG:MAI:EDGE:SOU {trigger_source}')

    def set_timing_scale(self, scale):
        """ Set the time base

        This defines the available display window, as 10
        divisions are displayed.

        :scale: Time per division (in s)
        """
        self.device.write(":HORIZONTAL:MAIN:SCALE {:e}".format(scale))

    def get_timing_scale(self):
        """ Get time base in secs per division"""

        res = self.device.query(":HORIZONTAL:MAIN:SCALE?")

        timing_res = re.compile(
             ':HORIZONTAL:MAIN:SCALE[ ]([0-9\.\+Ee-]+)'
            ).match(res).group(1)

        return float(timing_res)

    def set_single_run_acq(self):
        """Set acquisition mode to single run"""

        self.device.write('acquire:stopafter sequence')

    def acquire_single_run(self):
        """ Run single acquisition"""

        self.device.write('acquire:state on')

    def _check_channel(self, channel):
        """ CHeck if channel is in CHANNEL list"""

        if channel not in CHANNEL_LIST:
            self.log.error(
                f"The channel '{channel}' is not available, available channels are {CHANNEL_LIST}."
            )

    def unitize_trace(self, trace, trace_preamble):
        """Transform unitless trace to trace with units, constructs time array.

        :trace: (np.array) Unitless array as provided by oscilloscope
        :trace_preamble: (string) Waveform preamble.

        Returns trace, a np.array in correct units, ts, the time
        array in seconds, and y_unit, the unit of the Y-axis.
        """

        # Overcharged reges extracting all relevant paremters
        wave_pre_regex = 'NR_PT (?P<n_points>[0-9\.\+Ee-]+).+XINCR (?P<x_incr>[0-9\.\+Ee-]+).+PT_OFF (?P<pt_off>[0-9\.\+Ee-]+).+XZERO (?P<x_zero>[0-9\.\+Ee-]+).+XUNIT "(?P<x_unit>[^"]+).+YMULT (?P<y_mult>[0-9\.\+Ee-]+).+YZERO (?P<y_zero>[0-9\.\+Ee-]+).+YOFF (?P<y_off>[0-9\.\+Ee-]+).+YUNIT "(?P<y_unit>[^"]+)'

        wave_pre_matches = re.search(wave_pre_regex, trace_preamble)

        # Adjust trace as shown in the coding manual 2-255
        trace = (
            trace - float(wave_pre_matches['y_off'])
        ) * \
            float(wave_pre_matches['y_mult']) + \
            float(wave_pre_matches['y_zero'])

        # Construct timing array as shown in the coding manual 2-250
        ts = float(wave_pre_matches['x_zero']) + \
            (
                np.arange(int(wave_pre_matches['n_points'])) -
                int(wave_pre_matches['pt_off'])
            ) * float(wave_pre_matches['x_incr'])

        x_unit = wave_pre_matches['x_unit']
        y_unit = wave_pre_matches['y_unit']

        # Construct trace dictionary
        trace_dict = {
            'trace':    trace,
            'ts':       ts,
            'x_unit':   x_unit,
            'y_unit':   y_unit
        }

        return trace_dict

    def plot_traces(self, channel_list, curve_res=1, staggered=True):
        """Plot traces.

        :channel_list: (list or string) List of channel names.
        """

        # If only one channel provided, make a list out of it.
        if type(channel_list) is not list:
            channel_list = [channel_list]

        num_channels = len(channel_list)

        if not num_channels == 1 and staggered:

            if staggered:
                fig, axs = plt.subplots(num_channels, sharex=True, sharey=True)

                for i, channel in enumerate(channel_list):
                    trace_dict = self.read_out_trace(channel, curve_res)
                    axs[i].plot(trace_dict['ts']*1e6, trace_dict['trace'], label=channel)
                    fig.tight_layout()

                    axs[i].legend()

                y_unit = trace_dict['y_unit']
                fig.text(0.5, -0.04, r'Time since trigger [$\mu$s]', ha='center')
                fig.text(-0.04, 0.5, f"Signal [{y_unit}]", va='center', rotation='vertical')

        else:

            for i, channel in enumerate(channel_list):

                trace_dict = self.read_out_trace(channel, curve_res)
                plt.plot(trace_dict['ts']*1e6, trace_dict['trace'], label=channel)
                y_unit = trace_dict['y_unit']
                plt.xlabel(r'Time since trigger [$\mu$s]')
                plt.ylabel(f"Signal [{y_unit}]")
                plt.legend()

        plt.show()

    def read_out_trace(self, channel, curve_res=1):
        """ Read out trace

        :channel: Channel to read out (must be in CHANNEL_LIST)
        :curve_res: Bit resolution for returned data. If 1, value range is from -127 to 127,
            if 2, the value range is from -32768 to 32768.

        Returns np.array of sample points (in unit of Voltage divisions) and
        corresponding array of times (in seconds).
        """

        self._check_channel(channel)

        # Enable trace
        self.show_trace(channel)

        if curve_res not in [1, 2]:
            self.log.error("The bit resolution of the curve data must be either 1 or 2.")

        # Set curve data to desired bit
        self.device.write(f'DATa:WIDth {curve_res}')

        # Set trace we want to look at
        self.device.write(f'DATa:SOUrce {channel}')

        # Set encoding
        self.device.write('data:encdg ascii')

        # Read out trace
        res = self.device.query('curve?')

        # Tidy up curve
        raw_curve = res.replace(':CURVE', '').replace(' ', '').replace('\n', '')

        # Transform in numpy array
        trace = np.fromstring(raw_curve,  dtype=int, sep=',')

        # Read wave preamble
        wave_pre = self.device.query('WFMPre?')

        # Transform units of trace
        trace_dict = self.unitize_trace(trace, wave_pre)

        return trace_dict

    def show_trace(self, channel):
        """Display trace

        Required for trace readout.
        """

        self._check_channel(channel)

        self.device.write(f'SELect:{channel} 1')

    def hide_trace(self, channel):
        """Hide trace."""

        self._check_channel(channel)

        self.device.write(f'SELect:{channel} 0')
