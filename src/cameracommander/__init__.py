from loguru import logger
import sys

logger.add(sys.stderr, format="{time} {level} {extra[file_name]}:{line} {message}", filter=__name__, level="INFO")
logger = logger.patch(lambda record: record["extra"].update(file_name=record["name"]))
