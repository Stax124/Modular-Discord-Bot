import argparse
import logging
import os
import subprocess

import discord
from coloredlogs import install as install_coloredlogs
from discord.ext import commands
from discord.ext.commands import AutoShardedBot
from discord.ext.commands.context import Context
from discord.ext.commands.errors import (
    ExtensionAlreadyLoaded,
    ExtensionNotFound,
    ExtensionNotLoaded,
)
from pretty_help import PrettyHelp
from sqlmodel import Session, select
from sqlmodel.sql.expression import Select, SelectOfScalar

from api_commands import *
from api_commands.commands import commands as imported_commands
from core.functions import is_in_virtualenv
from core.plugin import Plugin
from core.plugin_handler import PluginHandler
from db import generate_engine, get_session
from models.config import Config
from models.guild import Guild

# Fix sqlalchemy caching with sqlmodel
SelectOfScalar.inherit_cache = True  # type: ignore
Select.inherit_cache = True  # type: ignore


loglevels = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL:": logging.CRITICAL,
}

# Searching for all primary modules
default_extensions = [
    "extensions." + i.replace(".py", "")
    for i in os.listdir("extensions")
    if i.endswith(".py")
]

if not os.path.exists("plugins"):
    os.makedirs("plugins")


def get_prefix(bot: "ModularBot", msg: discord.Message) -> list[str]:
    if msg.channel.type == discord.ChannelType.private:
        logging.debug("Private message, using default prefix")
        return commands.when_mentioned_or("!")(bot, msg)  # type: ignore
    else:
        if msg.guild == None:
            return commands.when_mentioned_or("!")(bot, msg)  # type: ignore

        statement = select(Guild).where(Guild.id == msg.guild.id)
        server = bot.database.exec(statement).first()
        prefix = server.prefix if server else "!"

        logging.debug(f"Using prefix {prefix} for this server")
        return commands.when_mentioned_or(prefix)(bot, msg)  # type: ignore


class ModularBot(AutoShardedBot):
    def __init__(self, enable_rce: bool = False, disable_plugins: bool = False) -> None:
        super().__init__(
            command_prefix=get_prefix,  # type: ignore
            help_command=PrettyHelp(
                color=0xFFFF00, show_index=True, sort_commands=True
            ),
            intents=discord.Intents.all(),
        )

        # State of the bot
        self.paused: bool = False
        self.__version__: str = "0.0.1alpha"

        # Database stuff
        self.engine = generate_engine()
        self.database: Session = get_session(self.engine)

        # Set up config for the bot
        self.setup_config()

        # Plugins
        self.disable_plugins: bool = disable_plugins
        self.plugins: list[Plugin] = []
        self.plugin_handler: PluginHandler = PluginHandler(self)
        if self.disable_plugins:
            logging.warning("Plugins are disabled")

        # Web
        self.web = subprocess.Popen("uvicorn web:app", shell=True)

        # Custom commands from web
        self.custom_commands = imported_commands

        if not disable_plugins:
            self.plugin_handler.populate_plugins()
            logging.info(f"Plugins: {[i.name for i in self.plugins]}")
            self.plugin_handler.install_requirements()
            self.plugin_handler.load_all_plugins()

        # RCE
        self.enable_rce: bool = enable_rce

    def run(self, token: str, *, bot: bool = True, reconnect: bool = True) -> None:
        super().run(token, bot=bot, reconnect=reconnect)

    def restart_web(self) -> None:
        self.web.kill()
        self.web = subprocess.Popen("python web.py", shell=True, cwd=os.getcwd())

    def setup_config(self) -> None:
        config = self.database.exec(select(Config)).first()

        if not config:
            logging.warning("No config found, creating one")
            self.database.add(Config())
            self.database.commit()
            logging.info("Config created")


if __name__ == "__main__":
    # Command line interface handling
    parser = argparse.ArgumentParser(
        prog="Trinity", description="Economy discord bot made in python"
    )
    parser.add_argument(
        "-l",
        "--logging",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Choose level of logging",
    )
    parser.add_argument("-f", "--file", type=str, help="Filename for logging")
    parser.add_argument(
        "--token",
        default=os.environ.get("TRINITY"),
        type=str,
        help="Discord API token: Get yours at https://discord.com/developers/applications",
    )
    parser.add_argument(
        "--enable-rce",
        action="store_true",
        help="Enable remote code execution for owner for fast debugging",
    )
    parser.add_argument(
        "--disable-plugins", action="store_true", help="Disable plugins"
    )
    args = parser.parse_args()

    if args.file:
        logging.basicConfig(
            level=loglevels[args.logging],
            handlers=[logging.FileHandler(args.file, "w", "utf-8")],
            format="%(levelname)s | %(asctime)s | %(name)s |->| %(message)s",
            datefmt=r"%H:%M:%S",
        )

    # Coloring the logs
    install_coloredlogs(
        level=loglevels[args.logging],
        fmt="%(levelname)s | %(asctime)s | %(name)s |->| %(message)s",
        datefmt=r"%H:%M:%S",
    )

    # Quit if not in virtualenv
    if not is_in_virtualenv():
        logging.error(
            "For security reasons, this bot should only be used in a virtualenv, please create one and run this script again"
        )
        exit(1)

    # Init bot and add necessary commands
    bot = ModularBot(enable_rce=args.enable_rce, disable_plugins=args.disable_plugins)

    @bot.command(name="reload")
    @commands.is_owner()
    async def command_reload_extension(ctx: Context, extension: str) -> None:
        try:
            bot.reload_extension(extension)
            logging.info(f"{extension} reloaded")
            embed = discord.Embed(color=0x00FF00, description=f"{extension} reloaded")
            embed.set_author(name="Reload", icon_url=bot.user.avatar_url.__str__())
        except ExtensionNotFound:
            logging.error(f"{extension} not found")
            embed = discord.Embed(color=0xFF0000, description=f"{extension} not found")
            embed.set_author(name="Reload", icon_url=bot.user.avatar_url.__str__())

        await ctx.send(embed=embed)

    @bot.command(name="load")
    @commands.is_owner()
    async def command_load_extension(ctx: Context, extension: str) -> None:
        try:
            bot.load_extension(extension)
            logging.info(f"{extension} loaded")
            embed = discord.Embed(color=0x00FF00, description=f"{extension} loaded")
            embed.set_author(name="Load", icon_url=bot.user.avatar_url.__str__())
        except ExtensionAlreadyLoaded:
            logging.warn(f"{extension} already loaded")
            embed = discord.Embed(
                color=0xFF0000, description=f"{extension} already loaded"
            )
            embed.set_author(name="Load", icon_url=bot.user.avatar_url.__str__())
        except ExtensionNotFound:
            logging.error(f"{extension} not found")
            embed = discord.Embed(color=0xFF0000, description=f"{extension} not found")
            embed.set_author(name="Load", icon_url=bot.user.avatar_url.__str__())

        await ctx.send(embed=embed)

    @bot.command(name="unload")
    @commands.is_owner()
    async def command_unload_extension(ctx: Context, extension: str) -> None:
        try:
            bot.unload_extension(extension)
            logging.info(f"{extension} unloaded")
            embed = discord.Embed(color=0x00FF00, description=f"{extension} unloaded")
            embed.set_author(name="Unload", icon_url=bot.user.avatar_url.__str__())
        except ExtensionNotFound:
            logging.error(f"{extension} not found")
            embed = discord.Embed(color=0xFF0000, description=f"{extension} not found")
            embed.set_author(name="Unload", icon_url=bot.user.avatar_url.__str__())
        except ExtensionNotLoaded:
            logging.error(f"{extension} exists, but is not loaded")
            embed = discord.Embed(
                color=0xFF0000, description=f"{extension} exists, but is not loaded"
            )
            embed.set_author(name="Unload", icon_url=bot.user.avatar_url.__str__())

        await ctx.send(embed=embed)

    @bot.command(name="reload-all")
    @commands.is_owner()
    async def command_reload_all_extensions(ctx: Context) -> None:
        all_extensions = [
            i.replace(".py", "") for i in os.listdir("extensions") if i.endswith(".py")
        ]

        ok = True

        for extension in all_extensions:
            try:
                bot.reload_extension(extension)
                logging.info(f"{extension} reloaded")
            except ExtensionNotFound:
                ok = False
                logging.error(f"{extension} not found")
                embed = discord.Embed(
                    color=0xFF0000, description=f"{extension} not found"
                )
                embed.set_author(
                    name="Reload All", icon_url=bot.user.avatar_url.__str__()
                )

        if ok:
            embed = discord.Embed(
                color=0x00FF00, description=f"All extensions reloaded"
            )
            embed.set_author(name="Reload All", icon_url=bot.user.avatar_url.__str__())

        await ctx.send(embed=embed)

    for extension in default_extensions:
        bot.load_extension(extension)
        logging.info(f"{extension} loaded")

    bot.run(args.token, reconnect=True)
