import logging


class Logger:
    @staticmethod
    def logger_msg(out):
        logger = logging.getLogger(__name__)
        logger.debug("-----------------")
        logger.debug(out)
        logger.debug("-----------------")

    @staticmethod
    def logger_msg(out):
        logger = logging.getLogger(__name__)
        logger.debug("-----------------")
        logger.debug(out)
        logger.debug("-----------------")

    @staticmethod
    def subprocess_log(function_name, out):
        logger = logging.getLogger(__name__)
        logger.error("########################################################################")
        logger.debug(function_name)
        logger.error("########################################################################")
        logger.debug(out.stdout)
        logger.warning(out.stderr)
        logger.error("########################################################################")
        logger.error("")