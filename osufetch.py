__version__ = "2.0.0"

import os
import re
import sys
import shutil
import argparse
import tempfile
import subprocess
import httpx
from configparser import ConfigParser
from ossapi import Ossapi
from datetime import datetime
from colorama import Fore, Style, init

init(autoreset=True)

CONFIG_DIR = os.path.expanduser("~/.config/osufetch")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")

DEFAULT_AVATAR_WIDTH = 24
DEFAULT_AVATAR_HEIGHT = 16

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def render_avatar_with_chafa(image_path, width=DEFAULT_AVATAR_WIDTH,
                              height=DEFAULT_AVATAR_HEIGHT, fmt="symbols"):
    """
    Render an image file to colored terminal output using chafa (libchafa's
    CLI frontend). Returns a list of lines (each possibly containing ANSI
    color codes), or None if chafa isn't available / rendering failed.
    """
    chafa_bin = shutil.which("chafa")
    if not chafa_bin:
        return None

    cmd = [
        chafa_bin,
        f"--size={width}x{height}",
        "--stretch",
        f"--format={fmt}",
        "--colors=full",
        image_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    lines = result.stdout.split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    return lines


def visible_len(s):
    """Length of a string as displayed, ignoring ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))

def load_or_create_config():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)

    config = ConfigParser()
    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
        if ("DEFAULT" not in config or
            not config["DEFAULT"].get("client_id") or
            not config["DEFAULT"].get("client_secret") or
            not config["DEFAULT"].get("user_id")):
            print("Config file is missing required fields. Recreating...")
            os.remove(CONFIG_FILE)
            return load_or_create_config()
        return config
    else:
        print("Welcome to osufetch first run setup!")
        print("To get your OAuth tokens, please visit:")
        print("https://osu.ppy.sh/home/account/edit\n")
        client_id = input("Enter your osu! OAuth Client ID: ").strip()
        client_secret = input("Enter your osu! OAuth Client Secret: ").strip()
        while True:
            user_id = input("Enter your osu! User ID (number): ").strip()
            if user_id.isdigit():
                break
            print("Invalid user ID, it must be a number.")
        config["DEFAULT"] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "user_id": user_id,
        }
        with open(CONFIG_FILE, "w") as f:
            config.write(f)
        print(f"Configuration saved to {CONFIG_FILE}\n")
        return config

def fetch_user_data(api, user_id):
    try:
        user = api.user(user_id)
        return user
    except Exception as e:
        print(f"Error fetching user data: {e}")
        sys.exit(1)


def download_avatar(avatar_url):
    """Download the user's avatar to a temp file and return its path, or
    None on failure."""
    if not avatar_url:
        return None
    try:
        with httpx.Client(http2=True, timeout=10, follow_redirects=True) as client:
            response = client.get(avatar_url)
            response.raise_for_status()
    except Exception as e:
        print(f"Warning: Failed to download avatar: {e}")
        return None

    content_type = response.headers.get("content-type", "")
    suffix = ".png"
    if "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    elif "gif" in content_type:
        suffix = ".gif"
    elif "webp" in content_type:
        suffix = ".webp"

    try:
        fd, path = tempfile.mkstemp(prefix="osufetch_avatar_", suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(response.content)
        return path
    except OSError as e:
        print(f"Warning: Failed to save avatar to disk: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="osufetch — terminal osu! profile")
    parser.add_argument("id", nargs="?", help="Specify osu! user ID/Name for this run only (does NOT overwrite config)")
    parser.add_argument("-v", "--version", action="version", version=f"{__version__}")
    parser.add_argument("--avatar-width", type=int, default=DEFAULT_AVATAR_WIDTH,
                         help=f"Avatar width in terminal cells (default: {DEFAULT_AVATAR_WIDTH})")
    parser.add_argument("--avatar-height", type=int, default=DEFAULT_AVATAR_HEIGHT,
                         help=f"Avatar height in terminal cells (default: {DEFAULT_AVATAR_HEIGHT})")
    parser.add_argument("--chafa-format", default="symbols",
                         choices=["symbols", "sixels", "kitty", "iterm2"],
                         help="chafa output format (default: symbols, works in any terminal)")
    parser.add_argument("--no-avatar", action="store_true",
                         help="Skip avatar rendering entirely (text info only)")
    args = parser.parse_args()

    config = load_or_create_config()

    client_id = config["DEFAULT"].get("client_id")
    client_secret = config["DEFAULT"].get("client_secret")

    user_id = args.id if args.id else config["DEFAULT"].get("user_id")

    if not user_id:
        print("Error: osu! user ID is missing or invalid.")
        sys.exit(1)

    if not user_id.isdigit() and not user_id.startswith("@"):
        user_id = f"@{user_id}"
    
    api = Ossapi(client_id, client_secret)
    user = fetch_user_data(api, user_id)

    if not user_id.isdigit():
        user_id = user.id

    playmode = user.playmode
    url = f"https://osuworld.octo.moe/api/users/{user_id}?mode={playmode}"
    with httpx.Client(http2=True) as client:
        response = client.get(url)
        data = response.json()

    def load_regions():
        url = "https://osuworld.octo.moe/locales/en/regions.json"
        try:
            with httpx.Client(http2=True, timeout=10) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.json()
                return data
        except Exception as e:
            print(f"Warning: Failed to fetch regions mapping: {e}")
            return {}

    regions = load_regions()

    region_id = data.get("region_id")

    state = "-"
    if region_id:
        region_id_str = str(region_id)
        country_code = region_id_str.split("-")[0]

        if country_code in regions:
            country_regions = regions[country_code]
            state = country_regions.get(region_id_str, "-")
        else:
            state = regions.get(region_id_str, "-")
    else:
        state = "—"

    grades = user.statistics.grade_counts

    avatar_path = None
    art_lines = ["(avatar disabled)"]

    if not args.no_avatar:
        avatar_url = getattr(user, "avatar_url", None)
        avatar_path = download_avatar(avatar_url)

        if avatar_path:
            rendered = render_avatar_with_chafa(
                avatar_path,
                width=args.avatar_width,
                height=args.avatar_height,
                fmt=args.chafa_format,
            )
            if rendered:
                art_lines = rendered
            else:
                art_lines = [
                    f"{Fore.RED}[chafa not found or render failed —{Style.RESET_ALL}",
                    f"{Fore.RED} install libchafa's `chafa` CLI]{Style.RESET_ALL}",
                ]
        else:
            art_lines = [f"{Fore.RED}[no avatar available]{Style.RESET_ALL}"]

    info_lines = [
        f"{Fore.CYAN}Username:{Style.RESET_ALL}       {Fore.WHITE}{user.username}{Style.RESET_ALL}",
        f"{Fore.CYAN}Also known as:{Style.RESET_ALL}  {Fore.WHITE}{", ".join(user.previous_usernames) if user.previous_usernames else "-"}{Style.RESET_ALL}",
        f"{Fore.CYAN}Country:{Style.RESET_ALL}        {Fore.WHITE}{f"{user.country.code} | {user.country.name}"}{Style.RESET_ALL}",
        f"{Fore.CYAN}State:{Style.RESET_ALL}          {Fore.WHITE}{state}{Style.RESET_ALL}",
        f"{Fore.CYAN}Playmode:{Style.RESET_ALL}       {Fore.WHITE}{playmode}{Style.RESET_ALL}",
        f"{Fore.CYAN}Team:{Style.RESET_ALL}           {Fore.WHITE}{f"{user.team.short_name} | {user.team.name}" if user.team else "-"}{Style.RESET_ALL}",
        f"{Fore.CYAN}PP:{Style.RESET_ALL}             {Fore.WHITE}{round(user.statistics.pp)}{Style.RESET_ALL}",
        f"{Fore.CYAN}Accuracy:{Style.RESET_ALL}       {Fore.WHITE}{round(user.statistics.hit_accuracy, 2)}%{Style.RESET_ALL}",
        f"{Fore.CYAN}Global Rank:{Style.RESET_ALL}    {Fore.WHITE}#{user.statistics.global_rank}{Style.RESET_ALL}",
        f"{Fore.CYAN}Country Rank:{Style.RESET_ALL}   {Fore.WHITE}#{user.statistics.country_rank}{Style.RESET_ALL}",
        f"{Fore.CYAN}State Rank:{Style.RESET_ALL}     {Fore.WHITE}#{data.get("placement", "-")}{Style.RESET_ALL}",
        f"{Fore.CYAN}Play Count:{Style.RESET_ALL}     {Fore.WHITE}{user.statistics.play_count}{Style.RESET_ALL}",
        f"{Fore.CYAN}Max Combo:{Style.RESET_ALL}      {Fore.WHITE}{user.statistics.maximum_combo}{Style.RESET_ALL}",
        f"{Fore.CYAN}Grades:{Style.RESET_ALL}         {Fore.WHITE}SS: {grades.ss} | SSH: {grades.ssh} | S: {grades.s} | SH: {grades.sh} | A: {grades.a}{Style.RESET_ALL}",
        f"{Fore.CYAN}Supporter:{Style.RESET_ALL}      {Fore.WHITE}{"Yes" if user.is_supporter else "No"}{Style.RESET_ALL}",
        f"{Fore.CYAN}Joined:{Style.RESET_ALL}         {Fore.WHITE}{user.join_date.date()}{Style.RESET_ALL}",
    ]

    if playmode == "mania":
        info_lines[6] = f"{Fore.CYAN}PP:{Style.RESET_ALL}             {Fore.WHITE}{round(user.statistics.pp)} (4K: {round(user.statistics.variants[0].pp)}, 7K: {round(user.statistics.variants[1].pp)}){Style.RESET_ALL}"
        info_lines[8] = f"{Fore.CYAN}Global Rank:{Style.RESET_ALL}    {Fore.WHITE}#{user.statistics.global_rank} (4K: #{user.statistics.variants[0].global_rank}, 7K: #{user.statistics.variants[1].global_rank}){Style.RESET_ALL}"
        info_lines[9] = f"{Fore.CYAN}Country Rank:{Style.RESET_ALL}   {Fore.WHITE}#{user.statistics.country_rank} (4K: #{user.statistics.variants[0].country_rank}, 7K: #{user.statistics.variants[1].country_rank}){Style.RESET_ALL}"

    art_width = max((visible_len(line) for line in art_lines), default=0)
    max_lines = max(len(art_lines), len(info_lines))
    for i in range(max_lines):
        art_line = art_lines[i] if i < len(art_lines) else ""
        info_line = info_lines[i] if i < len(info_lines) else ""
        padding = " " * max(0, art_width - visible_len(art_line))
        print(f"{art_line}{padding}  {info_line}")

    print()

    if avatar_path and os.path.exists(avatar_path):
        try:
            os.remove(avatar_path)
        except OSError:
            pass

if __name__ == "__main__":
    main()
