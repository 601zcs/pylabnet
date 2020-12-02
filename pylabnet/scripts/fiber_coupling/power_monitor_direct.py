import numpy as np
from si_prefix import split, prefix
import socket
import time

from pylabnet.gui.pyqt.external_gui import Window
from pylabnet.utils.logging.logger import LogHandler
import pyqtgraph as pg
from pylabnet.utils.helper_methods import generate_widgets, unpack_launcher, find_client, load_config, get_gui_widgets

# Time between power meter calls to prevent crashes
BUFFER = 1e-3

class Monitor:
    RANGE_LIST = [
        'AUTO', 'R1NW', 'R10NW', 'R100NW', 'R1UW', 'R10UW', 'R100UW', 'R1MW',
        'R10MW', 'R100MW', 'R1W', 'R10W', 'R100W', 'R1KW'
    ]

    def __init__(self, pm_client, gui='fiber_coupling', logger=None, calibration=None, name=None, port=None):
        """ Instantiates a monitor for 2-ch power meter with GUI

        :param pm_clients: (client, list of clients) clients of power meter
        :param gui_client: client of monitor GUI
        :param logger: instance of LogClient
        :calibration: (float) Calibration value for power meter.
        :name: (str) Humand-readable name of the power meter.
        """


        self.log = LogHandler(logger)
        self.gui =Window(
            gui_template=gui,
            host=socket.gethostbyname(socket.gethostname()),
            port=port
        )

        self.gui.apply_stylesheet()
        self.wavelength = []
        self.calibration = calibration
        self.name = name
        self.ir_index, self.rr_index = [], []
        self.pm = pm_client
        self.running = False
        self.num_plots = 3

        # Get all GUI widgets
        self.widgets = get_gui_widgets(
            self.gui,
            graph_widget=self.num_plots,
            number_widget=4,
            label_widget=2,
            name_label=1,
            combo_widget=2
        )

        self._initialize_gui()

    def sync_settings(self):
        """ Pulls current settings from PM and sets them to GUI """

        # Configure wavelength
        self.wavelength = self.pm.get_wavelength(1)

        self.widgets['number_widget'][-1].setValue(
            self.wavelength
        )

        # Configure Range to be Auto
        self.pm.set_range(1, self.RANGE_LIST[0])
        self.pm.set_range(2, self.RANGE_LIST[0])
        self.ir_index.append(0)
        self.rr_index.append(0)

    def update_settings(self, channel=0):
        """ Checks GUI for settings updates and implements

        :param channel: (int) channel of power meter to use
        """

        gui_wl = self.widgets['number_widget_4'].value()

        if self.wavelength[channel] != gui_wl:
            self.wavelength[channel] = gui_wl
            self.pm[channel].set_wavelength(1, self.wavelength[channel])
            self.pm[channel].set_wavelength(2, self.wavelength[channel])

        gui_ir = self.gui.get_item_index(f'ir_{channel}')
        if self.ir_index[channel] != gui_ir:
            self.ir_index[channel] = gui_ir
            self.pm[channel].set_range(2*channel+1, self.RANGE_LIST[self.ir_index[channel]])

        gui_rr = self.gui.get_item_index(f'rr_{channel}')
        if self.rr_index[channel] != gui_rr:
            self.rr_index[channel] = gui_rr
            self.pm[channel].set_range(2*channel+2, self.RANGE_LIST[self.rr_index[channel]])

    def run(self):
        # Continuously update data until paused
        self.running = True

        while self.running:
            time.sleep(BUFFER)
            self._update_output()
            self.gui.force_update()

    def _update_output(self):
            """ Runs the power monitor """

            # Check for/implement changes to settings
            #self.update_settings(0)

            # Get all current values
            try:
                p_in = self.pm.get_power(1)
                split_in = split(p_in)

            # Handle zero error
            except OverflowError:
                p_in = 0
                split_in = (0, 0)
            try:
                p_ref = self.pm.get_power(2)
                split_ref = split(p_ref)
            except OverflowError:
                p_ref = 0
                split_ref = (0, 0)
            try:
                efficiency = np.sqrt(p_ref/(p_in*self.calibration[0]))
            except ZeroDivisionError:
                efficiency = 0
            values = [p_in, p_ref, efficiency]

            # For the two power readings, reformat.
            # E.g., split(0.003) will return (3, -3)
            # And prefix(-3) will return 'm'
            formatted_values = [split_in[0], split_ref[0], efficiency]
            value_prefixes =  [prefix(split_val[1]) for split_val in [split_in, split_ref]]

            # Update GUI
            for plot_no in range(self.num_plots):
                # Update Number
                self.widgets['number_widget'][plot_no].setValue(formatted_values[plot_no])

                # Update Curve
                self.plotdata[plot_no] = np.append(self.plotdata[plot_no][1:], values[plot_no])
                self.widgets[f'curve_{plot_no}'].setData(self.plotdata[plot_no])

                if plot_no < 2:
                    self.widgets["label_widget"][plot_no].setText(f'{value_prefixes[plot_no]}W')

    def _initialize_gui(self):
        """ Instantiates GUI by assigning widgets """

        # Store plot data
        self.plotdata =[np.zeros(1000) for i in range(self.num_plots)]

        for plot_no in range(self.num_plots):
           # Create a curve and store the widget in our dictionary
            self.widgets[f'curve_{plot_no}'] = self.widgets['graph_widget'][plot_no].plot(
                pen=pg.mkPen(color=self.gui.COLOR_LIST[0])
            )


def launch(**kwargs):
    """ Launches the full fiber controll + GUI script """

    # Unpack and assign parameters
    logger, loghost, logport, clients, _, params = unpack_launcher(**kwargs)
    pm_client = find_client(logger, clients, 'thorlabs_pm320e')

    # Unpack settings
    settings = load_config(
        kwargs['config'],
        logger=logger
    )

    calibration = [settings['calibration']]
    name = settings['name']

    # Instantiate controller
    control = Monitor(
        pm_client=pm_client,
        logger=logger,
        calibration=calibration,
        name=name,
        port=logport
    )

    time.sleep(2)
    control.sync_settings()


    control.run()

    # Mitigate warnings about unused variables
    if loghost and logport and params:
        pass
