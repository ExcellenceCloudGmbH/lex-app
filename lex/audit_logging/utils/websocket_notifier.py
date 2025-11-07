"""
WebSocket notification handler for the CalculationLog system.

This module provides WebSocket notification functionality with proper error handling
and graceful degradation when WebSocket operations fail.
"""

import logging
from typing import Optional
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
# from lex.logging.utils.data_models import WebSocketError


logger = logging.getLogger(__name__)


class WebSocketNotifier:
    """
    Handles WebSocket notifications for calculation updates.
    
    This class provides static methods for sending WebSocket notifications
    with proper error handling and graceful failure recovery.
    """
    
    @staticmethod
    def send_calculation_update(calculation_record: str, calculation_id: str) -> bool:
        """
        Send WebSocket notification for calculation update.
        
        Maintains compatibility with existing "calculations" group messaging
        by sending notifications in the expected format.
        
        Args:
            calculation_record: The calculation record identifier (e.g., "model_name_id")
            calculation_id: The unique calculation identifier
            
        Returns:
            bool: True if notification was sent successfully, False otherwise
            
        Raises:
            WebSocketError: Only in critical scenarios where error propagation is needed
        """
        try:
            channel_layer = get_channel_layer()
            
            if not channel_layer:
                logger.warning(
                    "Channel layer not available for WebSocket notification",
                    extra={
                        "calculation_record": calculation_record,
                        "calculation_id": calculation_id,
                    }
                )
                return False
            
            # Create message in the format expected by existing consumers
            calc_id_message = {
                "type": "calculation_id",
                "payload": {
                    "calculation_record": calculation_record,
                    "calculation_id": calculation_id,
                },
            }
            
            # Send to the "calculations" group to maintain compatibility
            async_to_sync(channel_layer.group_send)("calculations", calc_id_message)
            
            logger.debug(
                "WebSocket notification sent successfully",
                extra={
                    "calculation_record": calculation_record,
                    "calculation_id": calculation_id,
                    "group": "calculations"
                }
            )
            
            return True
            
        except Exception as e:
            # Log the error but don't raise it to maintain graceful degradation
            logger.error(
                f"Failed to send WebSocket notification: {str(e)}",
                extra={
                    "calculation_record": calculation_record,
                    "calculation_id": calculation_id,
                    "error": str(e)
                },
                exc_info=True
            )
            
            # Return False to indicate failure, but don't raise exception
            # This allows the calling code to continue functioning
            return False
    
    @staticmethod
    def send_calculation_notification(payload: dict, group: str = "calculations") -> bool:
        """
        Send a general calculation notification to a WebSocket group.
        
        This method provides a more flexible interface for sending custom
        notification payloads to WebSocket groups.
        
        Args:
            payload: The notification payload to send
            group: The WebSocket group to send to (defaults to "calculations")
            
        Returns:
            bool: True if notification was sent successfully, False otherwise
        """
        try:
            channel_layer = get_channel_layer()
            
            if not channel_layer:
                logger.warning(
                    f"Channel layer not available for notification to group '{group}'",
                    extra={"group": group, "payload": payload}
                )
                return False
            
            message = {
                "type": "calculation_notification",
                "payload": payload
            }
            
            async_to_sync(channel_layer.group_send)(group, message)
            
            logger.debug(
                f"Calculation notification sent to group '{group}'",
                extra={"group": group, "payload": payload}
            )
            
            return True
            
        except Exception as e:
            logger.error(
                f"Failed to send calculation notification to group '{group}': {str(e)}",
                extra={
                    "group": group,
                    "payload": payload,
                    "error": str(e)
                },
                exc_info=True
            )
            
            return False
    
    @staticmethod
    def is_websocket_available() -> bool:
        """
        Check if WebSocket functionality is available.
        
        Returns:
            bool: True if channel layer is available, False otherwise
        """
        try:
            channel_layer = get_channel_layer()
            return channel_layer is not None
        except Exception as e:
            logger.debug(f"WebSocket availability check failed: {str(e)}")
            return False