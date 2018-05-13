#!/usr/bin/env python3
# Coin Price indicator
#
# Nil Gradisnik <nil.gradisnik@gmail.com>
# Sander Van de Moortel <sander.vandemoortel@gmail.com>
#

import signal
import yaml
import logging
import glob
import dbus
import importlib
import notify2

from os.path import abspath, dirname, isfile, basename
from indicator import Indicator
from async_downloader import AsyncDownloadService
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import Gtk, GdkPixbuf

try:
    from gi.repository import AppIndicator3 as AppIndicator
except ImportError:
    from gi.repository import AppIndicator


PROJECT_ROOT = abspath(dirname(dirname(__file__)))
SETTINGS_FILE = PROJECT_ROOT + '/user.conf'


class Coin():
    config = yaml.load(open(PROJECT_ROOT + '/config.yaml', 'r'))
    config['project_root'] = PROJECT_ROOT

    def __init__(self):
        self.downloader = AsyncDownloadService()
        self.unique_id = 0

        self._load_exchanges()
        self._load_assets()
        self._load_settings()
        self._start_main()

        self.instances = []
        self.discoveries = 0
        self._add_many_indicators(self.settings.get('tickers'))
        self._start_gui()

    # Load exchange 'plug-ins' from exchanges dir
    def _load_exchanges(self):
        dirfiles = glob.glob(dirname(__file__) + "/exchanges/*.py")
        plugins = [basename(f)[:-3] for f in dirfiles if isfile(f) and not f.endswith('__init__.py')]
        plugins.sort()

        self.EXCHANGES = []
        for plugin in plugins:
            class_name = plugin.capitalize()
            class_ = getattr(importlib.import_module('exchanges.' + plugin), class_name)
            self.EXCHANGES.append(class_)

    # Find an exchange
    def find_exchange_by_code(self, code):
        for exchange in self.EXCHANGES:
            if exchange.get_code() == code.lower():
                return exchange

    # Creates a structure of available assets (from_currency > to_currency > exchange)
    def _load_assets(self):
        self.assets = {}

        for exchange in self.EXCHANGES:
            self.assets[exchange.get_code()] = exchange.get_asset_pairs()

        # inverse the hierarchy for easier asset selection
        bases = {}
        for exchange in self.assets:
            for asset_pair in self.assets.get(exchange):
                base = asset_pair.get('base')
                quote = asset_pair.get('quote')

                if base not in bases:
                    bases[base] = {}

                if quote not in bases[base]:
                    bases[base][quote] = []

                bases[base][quote].append(self.find_exchange_by_code(exchange))

        self.bases = bases

    # load instances
    def _load_settings(self):
        self.settings = {}
        # load from file
        if isfile(SETTINGS_FILE):
            self.settings = yaml.load(open(SETTINGS_FILE, 'r'))

        # set defaults if settings not defined
        if not self.settings.get('tickers'):
            self.settings['tickers'] = [{
                'exchange': self.EXCHANGES[0].get_code(),
                'asset_pair': self.assets[self.EXCHANGES[0].get_code()][0].get('pair'),
                'refresh': 3,
                'default_label': self.EXCHANGES[0].get_default_label()
            }]

        if not self.settings.get('recent'):
            self.settings['recent'] = ['BTC', 'ETH', 'XRP']

    # saves settings for each ticker
    def save_settings(self):
        tickers = []
        for instance in self.instances:
            ticker = {
                'exchange': instance.exchange.get_code(),
                'asset_pair': instance.exchange.asset_pair.get('pair'),
                'refresh': instance.refresh_frequency,
                'default_label': instance.default_label
            }
            tickers.append(ticker)
            self.settings['tickers'] = tickers

        try:
            with open(SETTINGS_FILE, 'w') as handle:
                yaml.dump(self.settings, handle, default_flow_style=False)
        except IOError:
            logging.error('Settings file not writable')

    # # Add a new base to the recents settings, and push the last one off the edge
    # def add_new_recent_base(self, base):
    #     if base in self.settings['recent']:
    #         self.settings['recent'].remove(base)

    #     self.settings['recent'] = self.settings['recent'][0:4]
    #     self.settings['recent'].insert(0, base)

    #     for instance in self.instances:
    #         instance.rebuild_recents_menu()

    # Start the main indicator icon and its menu
    def _start_main(self):
        print(self.config.get('app').get('name') + ' v' + self.config.get('app').get('version') + " running!")

        self.icon = self.config.get('project_root') + '/resources/icon_32px.png'
        self.main_item = AppIndicator.Indicator.new(
            self.config.get('app').get('name'), self.icon, AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.main_item.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.main_item.set_ordering_index(0)
        self.main_item.set_menu(self._menu())

    def _start_gui(self):
        signal.signal(signal.SIGINT, Gtk.main_quit)  # ctrl+c exit
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        bus.add_signal_receiver(
            self.handle_resume,
            None,
            'org.freedesktop.login1.Manager',
            'org.freedesktop.login1'
        )
        Gtk.main()

    # Program main menu
    def _menu(self):
        menu = Gtk.Menu()

        self.add_item = Gtk.MenuItem("Add Ticker")
        self.discover_item = Gtk.MenuItem("Discover Assets")
        self.about_item = Gtk.MenuItem("About")
        self.quit_item = Gtk.MenuItem("Quit")

        self.add_item.connect("activate", self._add_ticker)
        self.discover_item.connect("activate", self._discover_assets)
        self.about_item.connect("activate", self._about)
        self.quit_item.connect("activate", self._quit_all)

        menu.append(self.add_item)
        menu.append(self.discover_item)
        menu.append(self.about_item)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self.quit_item)
        menu.show_all()

        return menu

    # Adds a ticker and starts it
    def _add_indicator(self, settings):
        exchange = settings.get('exchange')
        refresh = settings.get('refresh')
        asset_pair = settings.get('asset_pair')
        default_label = settings.get('default_label')
        self.unique_id += 1
        indicator = Indicator(self, self.unique_id, exchange, asset_pair, refresh, default_label)
        self.instances.append(indicator)
        indicator.start()

    # adds many tickers
    def _add_many_indicators(self, tickers):
        for ticker in tickers:
            self._add_indicator(ticker)

    # Menu item to add a ticker
    def _add_ticker(self, widget):
        self._add_indicator(self.settings.get('tickers')[len(self.settings.get('tickers')) - 1])
        self.save_settings()

    # Remove ticker
    def remove_ticker(self, indicator):
        if len(self.instances) == 1:  # is it the last ticker?
            Gtk.main_quit()  # then quit entirely
        else:  # otherwise just remove this one
            indicator.exchange.stop()
            del indicator.indicator_widget
            self.instances.remove(indicator)
            self.save_settings()

    # Menu item to download any new assets from the exchanges
    def _discover_assets(self, widget):
        self.main_item.set_icon(self.config.get('project_root') + '/resources/loading.png')

        for indicator in self.instances:
            if indicator.asset_selection_window:
                indicator.asset_selection_window.destroy()

        for exchange in self.EXCHANGES:
            exchange.discover_assets(self.downloader, self.update_assets)

    # When discovery completes, reload currencies and rebuild menus of all instances
    def update_assets(self):
        self.discoveries += 1
        if self.discoveries < len(self.EXCHANGES):
            return  # wait until all exchanges finish discovery

        self.discoveries = 0
        self._load_assets()

        if notify2.init(self.config.get('app').get('name')):
            n = notify2.Notification(self.config.get('app').get('name'), "Finished discovering new assets", self.icon)
            n.set_urgency(1)
            n.timeout = 2000
            n.show()

        self.main_item.set_icon(self.icon)

    # Handle system resume by refreshing all tickers
    def handle_resume(self, sleeping, *args):
        if not sleeping:
            for instance in self.instances:
                instance.exchange.stop().start()

    # Shows an About dialog
    def _about(self, widget):
        logo_124px = GdkPixbuf.Pixbuf.new_from_file(self.config.get('project_root') + '/resources/icon_32px.png')
        about = Gtk.AboutDialog()
        about.set_program_name(self.config.get('app').get('name'))
        about.set_comments(self.config.get('app').get('description'))
        about.set_version(self.config.get('app').get('version'))
        about.set_website(self.config.get('app').get('url'))
        authors = []
        for author in self.config.get('authors'):
            authors.append(author.get('name') + ' <' + author.get('email') + '>')
        about.set_authors(authors)
        contributors = []
        for contributor in self.config.get('contributors'):
            contributors.append(contributor.get('name') + ' <' + contributor.get('email') + '>')
        about.add_credit_section('Exchange plugins', contributors)
        about.set_artists([self.config.get('artist').get('name') + ' <' + self.config.get('artist').get('email') + '>'])
        about.set_license_type(Gtk.License.MIT_X11)
        about.set_logo(logo_124px)
        about.set_keep_above(True)
        res = about.run()
        if res == -4 or -6:  # close events
            about.destroy()

    # Menu item to remove all tickers and quits the application
    def _quit_all(self, widget):
        Gtk.main_quit()


coin = Coin()
