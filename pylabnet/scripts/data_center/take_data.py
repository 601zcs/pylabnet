""" Module for taking data using arbitrary experiment scripts
    and Dataset objects """

import socket
import os
import sys
import inspect
import importlib
import time
import pickle
from datetime import datetime
from PyQt5 import QtWidgets, QtGui, QtCore

from pylabnet.utils.logging.logger import LogHandler
from pylabnet.gui.pyqt.external_gui import Window
from pylabnet.utils.helper_methods import load_config, generic_save, unpack_launcher
from pylabnet.scripts.data_center import datasets


REFRESH_RATE = 75   # refresh rate in ms, try increasing if GUI lags

class DataTaker:

    def __init__(self, logger=None, clients={}, config=None):

        self.log = LogHandler(logger)
        self.dataset = None

        # Instantiate GUI window
        self.gui = Window(
            gui_template='data_taker',
            host=socket.gethostbyname(socket.gethostname())
        )

        # Configure list of experiments
        self.gui.config.setText(config)
        self.config = load_config(config, logger=self.log)
        self.exp_path = self.config['exp_path']
        if self.exp_path is None:
            self.exp_path = os.getcwd()
        sys.path.insert(1, self.exp_path)
        self.update_experiment_list()

        # Configure list of clients
        self.clients = clients
        for client_name, client_obj in self.clients.items():
            client_item = QtWidgets.QListWidgetItem(client_name)
            client_item.setToolTip(str(client_obj))
            self.gui.clients.addItem(client_item)

        # Configure dataset menu
        for name, obj in inspect.getmembers(datasets):
            if inspect.isclass(obj) and issubclass(obj, datasets.Dataset):
                self.gui.dataset.addItem(name)

        # Configure button clicks
        self.gui.configure.clicked.connect(self.configure)
        self.gui.run.clicked.connect(self.run)
        self.gui.save.clicked.connect(self.save)
        self.gui.load_config.clicked.connect(self.reload_config)
        self.gui.showMaximized()
        self.gui.apply_stylesheet()

    def update_experiment_list(self):
        """ Updates list of experiments """

        self.gui.exp.clear()
        for filename in os.listdir(self.exp_path):
            if filename.endswith('.py'):
                self.gui.exp.addItem(filename[:-3])
        self.gui.exp.itemClicked.connect(self.display_experiment)

    def display_experiment(self, item):
        """ Displays the currently clicked experiment in the text browser

        :param item: (QlistWidgetItem) with label of name of experiment to display
        """

        with open(os.path.join(self.exp_path, f'{item.text()}.py'), 'r') as exp_file:
            exp_content = exp_file.read()

        self.gui.exp_preview.setText(exp_content)
        self.gui.exp_preview.setStyleSheet('font: 10pt "Consolas"; '
                                           'color: rgb(255, 255, 255); '
                                           'background-color: rgb(0, 0, 0);')
        self.log.update_metadata(experiment_file=exp_content)

    def configure(self):
        """ Configures the currently selected experiment + dataset """

        # If the experiment is running, do nothing
        try:
            if self.experiment_thread.isRunning():
                self.log.warn('Did not configure experiment, since it '
                              'is still in progress')
                return
        except:
            pass

        # Set all experiments to normal state and highlight configured expt
        for item_no in range(self.gui.exp.count()):
            self.gui.exp.item(item_no).setBackground(QtGui.QBrush(QtGui.QColor('black')))
        self.gui.exp.currentItem().setBackground(QtGui.QBrush(QtGui.QColor('darkRed')))
        exp_name = self.gui.exp.currentItem().text()
        self.module = importlib.import_module(exp_name)
        self.module = importlib.reload(self.module)

        # Clear graph area and set up new or cleaned up dataset
        for index in reversed(range(self.gui.graph_layout.count())):
            try:
                self.gui.graph_layout.itemAt(index).widget().deleteLater()
            except AttributeError:
                try:
                    self.gui.graph_layout.itemAt(index).layout().deleteLater()
                except AttributeError:
                    pass
        self.gui.windows = {}
            # If we're not setting up a new measurement type, just clear the data
        self.dataset = getattr(datasets, self.gui.dataset.currentText())(
            gui=self.gui,
            log=self.log,
            config=self.config
        )


        # Run any pre-experiment configuration
        try:
            self.module.configure(dataset=self.dataset, **self.clients)
        except AttributeError:
            pass
        self.experiment = self.module.experiment

        self.log.info(f'Experiment {exp_name} configured')
        self.gui.exp_preview.setStyleSheet('font: 10pt "Consolas"; '
                                           'color: rgb(255, 255, 255); '
                                           'background-color: rgb(50, 50, 50);')

    def run(self):
        """ Runs/stops the experiment """

        # Run experiment
        if self.gui.run.text() == 'Run':
            self.gui.run.setStyleSheet('background-color: red')
            self.gui.run.setText('Stop')
            self.log.info('Experiment started')

            # Run update thread
            self.update_thread = UpdateThread()
            self.update_thread.data_updated.connect(self.dataset.update)

            self.experiment_thread = ExperimentThread(
                self.experiment,
                dataset=self.dataset,
                gui=self.gui,
                **self.clients
            )

            self.experiment_thread.status_flag.connect(self.dataset.interpret_status)
            self.experiment_thread.finished.connect(self.stop)
            self.log.update_metadata(
                exp_start_time=datetime.now().strftime('%d/%m/%Y %H:%M:%S:%f')
            )

        # Stop experiment
        else:
            self.experiment_thread.running = False

    def stop(self):
        """ Stops the experiment"""

        self.gui.run.setStyleSheet('background-color: green')
        self.gui.run.setText('Run')
        self.log.info('Experiment stopped')
        self.update_thread.running = False

        self.log.update_metadata(
                exp_stop_time=datetime.now().strftime('%d/%m/%Y %H:%M:%S:%f')
            )

        # Autosave if relevant
        if self.gui.autosave.isChecked():
            self.save()

    def save(self):
        """ Saves data """

        self.log.update_metadata(notes=self.gui.notes.toPlainText())
        self.dataset.save(
            filename=self.gui.save_name.text(),
            directory=self.config['save_path'],
            date_dir=True
        )
        self.log.info('Data saved')

    def reload_config(self):
        """ Loads a new config file """

        self.config=load_config(
            self.gui.config.text(), logger=self.log
        )


class ExperimentThread(QtCore.QThread):
    """ Thread that simply runs the experiment repeatedly """

    # Flag to monitor whether experiment alarm goes off
    status_flag = QtCore.pyqtSignal(str)

    def __init__(self, experiment, **params):
        self.experiment = experiment
        self.params = params
        self.running = True
        super().__init__()
        self.start()

    def run(self):
        while self.running:
            self.experiment(
                thread = self,
                status_flag=self.status_flag,
                **self.params)


class UpdateThread(QtCore.QThread):
    """ Thread that continuously signals GUI to update data """

    data_updated = QtCore.pyqtSignal()

    def __init__(self):
        self.running = True
        super().__init__()
        self.start()

    def run(self):
        while self.running:
            self.data_updated.emit()
            self.msleep(REFRESH_RATE)


def main():
    control = DataTaker(config='laser_scan')
    control.gui.app.exec_()

def launch(**kwargs):

    logger, loghost, logport, clients, guis, params = unpack_launcher(**kwargs)

    # Instantiate Monitor script
    control = DataTaker(
        logger=logger,
        clients=clients,
        config=kwargs['config'],
    )
    control.gui.set_network_info(port=kwargs['server_port'])

    # Run continuously
    control.gui.app.exec_()

if __name__== '__main__':
    main()
