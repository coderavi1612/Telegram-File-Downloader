import argparse
import logging
import os
import re
import time
from mimetypes import guess_extension

from tqdm import tqdm
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient, sync
from telethon.tl.types import MessageMediaPhoto
from FastTelethonhelper.FastTelethon import download_file

# Load environment variables from the .env file
load_dotenv()

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

# Initialize the Telegram client with a session name to save the session data
try:
    loop = asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

telegram_client = TelegramClient("session_name", API_ID, API_HASH, loop=loop)

PROGRESS_STATE = {
    "status": "idle",
    "current_file": "",
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "speed": "0 B/s",
    "logs": [],
    "errors": []
}

class WebUILogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        PROGRESS_STATE["logs"].append(msg)
        if record.levelno >= logging.ERROR:
            PROGRESS_STATE["errors"].append(msg)

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Clear existing handlers
if logger.hasHandlers():
    logger.handlers.clear()

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

web_handler = WebUILogHandler()
web_handler.setFormatter(formatter)
logger.addHandler(web_handler)

# Supported file categories
# Note: These are manually curated based on common use cases.
# Feel free to add or remove extensions as needed.
FILE_CATEGORIES = {
    "images": [
        "jpg",
        "jpeg",
        "png",
        "gif",
        "bmp",
        "webp",
        "svg",
        "heic",
        "raw",
    ],
    "documents": [
        "pdf",
        "doc",
        "docx",
        "odt",
        "rtf",
        "xls",
        "xlsx",
        "csv",
        "ppt",
        "pptx",
        "txt",
        "epub",
    ],
    "videos": [
        "mp4",
        "mkv",
        "avi",
        "mov",
        "wmv",
    ],
    "audios": [
        "mp3",
        "wav",
        "aac",
        "flac",
        "ogg",
        "m4a",
    ],
    "archives": [
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "bz2",
    ],
}


def sanitize_filename(text):
    """Sanitize a string to be safe for use as a filename."""
    if not text:
        return ""
        
    # Try to extract Index and Title to make cleaner, shorter names
    index_match = re.search(r'Index\s*[»\->:]*\s*(\d+)', text, re.IGNORECASE)
    title_match = re.search(r'Title\s*[»\->:]*\s*(.*?)(?=\n|➭|•|Batch|𝐁𝐚𝐭𝐜𝐡|Quality|$)', text, re.IGNORECASE)
    
    if index_match and title_match:
        idx = index_match.group(1).strip()
        title = title_match.group(1).strip()
        text = f"{idx} - {title}"

    # Replace newlines and tabs with spaces
    text = str(text).replace('\n', ' ').replace('\r', '').replace('\t', ' ')
    # Replace invalid filename characters (Mac/Windows/Linux safe-ish)
    text = re.sub(r'[<>:"/\\|?*]', '_', text)
    # Remove extra spaces
    text = re.sub(r'\s+', ' ', text).strip()
    # Keep path well under OS limits by slicing at 150 chars
    return text[:150]


def cleanup_incomplete_files(output_dir):
    """Remove any leftover .tmp files from interrupted downloads."""
    for file in os.listdir(output_dir):
        if file.endswith(".tmp"):
            temp_file_path = os.path.join(output_dir, file)
            logging.warning(f"Removing incomplete file: {temp_file_path}")
            os.remove(temp_file_path)


def create_directory_if_needed(directory):
    """Creates the directory if it does not exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)

def human_readable_size(size, decimal_places=2):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size < 1024.0 or unit == 'PB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"

def check_and_download_file(message, file_path):
    """Downloads the file with a temporary name and renames it after completion."""
    try:
        # Skip downloading if the file already exists
        if os.path.exists(file_path):
            logging.info(f"File already exists: {file_path}, skipping download.")
            return file_path, os.path.getsize(file_path)

        temp_file_path = file_path + ".tmp"
        downloaded_file = False

        with tqdm(
            total=message.file.size if hasattr(message, 'file') and message.file else None,
            desc=os.path.basename(file_path),
            unit='B', unit_scale=True, unit_divisor=1024,
            leave=True
        ) as pbar:
            start_time = time.time()
            last_time = start_time
            last_bytes = 0

            PROGRESS_STATE["status"] = "downloading"
            PROGRESS_STATE["current_file"] = os.path.basename(file_path)
            PROGRESS_STATE["downloaded_bytes"] = 0
            PROGRESS_STATE["total_bytes"] = pbar.total or 0

            def progress_callback(current, total):
                nonlocal last_time, last_bytes
                
                if pbar.total is None and total:
                    pbar.total = total
                    PROGRESS_STATE["total_bytes"] = total
                    
                pbar.update(current - pbar.n)
                
                # Update progress state for UI
                PROGRESS_STATE["downloaded_bytes"] = current
                
                current_time = time.time()
                time_diff = current_time - last_time
                if time_diff >= 0.5:  # Update speed roughly twice a second
                    speed_bps = (current - last_bytes) / time_diff
                    PROGRESS_STATE["speed"] = f"{human_readable_size(speed_bps)}/s"
                    last_time = current_time
                    last_bytes = current

            # Use FastTelethonhelper parallel downloader for massive speedup
            with open(temp_file_path, "wb") as f:
                telegram_client.loop.run_until_complete(
                    download_file(
                        client=telegram_client,
                        location=message.document if message.document else message.photo,
                        out=f,
                        progress_callback=progress_callback
                    )
                )
            downloaded_file = True

        PROGRESS_STATE["status"] = "idle"
        # Validate file size before renaming to ensure download was successful
        if (
            downloaded_file
            and os.path.exists(temp_file_path)
            and os.path.getsize(temp_file_path) > 0
        ):
            os.rename(temp_file_path, file_path)
            logging.info(f"Downloaded: {file_path}")
            return file_path, os.path.getsize(file_path)
        else:
            logging.warning(
                f"Downloaded file is incomplete or missing: {temp_file_path}"
            )
            if os.path.exists(temp_file_path):
                os.remove(temp_file_path)
    except Exception as error:
        logging.error(f"Failed to download file: {error}")
    return None, 0


def list_dialogs():
    """List all accessible dialogs (channels, groups, chats) with their details."""
    with telegram_client:
        logging.info("Fetching your dialogs...")
        print(f"\n{'ID':<15} {'Type':<25} {'Name':<30} {'Username':<20}")
        print("-" * 90)
        for dialog in telegram_client.iter_dialogs():
            dialog_id = dialog.id
            dialog_type = type(dialog.entity).__name__
            dialog_name = dialog.name or "(No name)"
            dialog_username = getattr(dialog.entity, "username", "") or "(No username)"

            # Check if it's a channel and whether it's public or private
            if dialog_type == "Channel":
                if hasattr(dialog.entity, "username") and dialog.entity.username:
                    dialog_type = "Channel (public)"
                else:
                    dialog_type = "Channel (private)"

            print(
                f"{dialog_id:<15} {dialog_type:<25} {dialog_name:<30} {dialog_username:<20}"
            )


def resolve_entity(entity_identifier):
    """
    Convert entity identifier to proper format for Telethon.
    Converts numeric strings to integers, keeps usernames and keywords as strings.

    Args:
        entity_identifier: Channel name, username, or numeric ID as string

    Returns:
        Integer for numeric IDs, string for usernames/keywords
    """
    try:
        return int(entity_identifier)
    except (ValueError, TypeError):
        # Keep as string for usernames like '@channel' or 'me'
        return entity_identifier


def download_files_from_entity(
    entity_identifier, file_type=None, save_directory=".", message_limit=0, topic_id=None
):
    create_directory_if_needed(save_directory)
    cleanup_incomplete_files(save_directory)

    total_file_size = 0
    total_files_downloaded = 0
    # message_limit of 0 now properly becomes None, meaning "get all messages"
    message_limit = None if message_limit == 0 else message_limit

    # Resolve the entity (convert to int if numeric, keep as string otherwise)
    entity = resolve_entity(entity_identifier)

    with telegram_client:
        logging.info(f"Fetching messages from: {entity_identifier}{f' (Topic ID: {topic_id})' if topic_id else ''}")
        # Fetching messages (filtered by topic if provided)
        messages = telegram_client.iter_messages(entity, limit=message_limit, reply_to=topic_id)

        for message in messages:
            if message.media:
                if isinstance(message.media, MessageMediaPhoto):
                    if not file_type or file_type.lower() == "images":
                        base_name = sanitize_filename(message.message) if message.message else str(message.id)
                        if not base_name:
                            base_name = str(message.id)
                        file_name = f"{base_name}.jpg"
                        file_path = os.path.join(save_directory, file_name)
                        downloaded_file, file_size = check_and_download_file(
                            message, file_path
                        )
                        if downloaded_file:
                            total_file_size += file_size
                            total_files_downloaded += 1

                elif message.file:
                    mime_type = message.file.mime_type
                    file_extension = guess_extension(mime_type) if mime_type else None
                    if file_extension is None:
                        file_extension = ""

                    base_name = sanitize_filename(message.message) if message.message else ""
                    if not base_name:
                        base_name = message.file.name or str(message.id)
                        # Remove existing extension from base_name if it has one and we are going to append it
                        if file_extension and base_name.endswith(file_extension):
                            base_name = base_name[:-len(file_extension)]
                    
                    file_name = f"{base_name}{file_extension}"

                    file_path = os.path.join(save_directory, file_name)

                    if (
                        not file_type
                        or (
                            file_type.lower() in FILE_CATEGORIES
                            and file_extension.lstrip(".")
                            in FILE_CATEGORIES.get(file_type.lower(), [])
                        )
                        or (file_extension.lstrip(".") == file_type.lower())
                    ):
                        downloaded_file, file_size = check_and_download_file(
                            message, file_path
                        )
                        if downloaded_file:
                            total_file_size += file_size
                            total_files_downloaded += 1

    logging.info(
        f"\nSummary: Total files downloaded: {total_files_downloaded}, Total size: {total_file_size / (1024 * 1024):.2f} MB"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download files from a Telegram entity (channel, group, chat, or user)."
    )
    parser.add_argument(
        "entity",
        nargs="?",
        type=str,
        help="The username or ID of the Telegram entity (channel, group, chat, or 'me' for saved messages).",
    )
    parser.add_argument(
        "-f",
        "--format",
        type=str,
        help=f"Filter by file type category or specific extension. "
        f"Categories: {', '.join(FILE_CATEGORIES.keys())}. "
        f"Or use specific extensions (e.g., pdf, jpg, mp4). "
        f"If omitted, all media types are downloaded.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=".",
        help="The directory to save downloaded files. Defaults to the current directory.",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=0,
        help="Maximum number of messages to check (not files to download). Use 0 for no limit. Defaults to 0 (all messages).",
    )
    parser.add_argument(
        "-t",
        "--topic",
        type=int,
        help="The topic (thread) ID to download from. Useful for downloading from a specific topic in a supergroup.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all accessible dialogs (channels, groups, chats) and exit.",
    )

    args = parser.parse_args()

    try:
        if args.list:
            list_dialogs()
        elif args.entity:
            download_files_from_entity(
                args.entity, args.format, args.output, args.limit, args.topic
            )
        else:
            parser.error(
                "Please specify an entity or use --list to view available dialogs (channels, groups, chats)."
            )
    except Exception as error:
        logging.error(f"An error occurred: {error}")
