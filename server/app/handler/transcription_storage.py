"""Handler for saving conversation transcriptions to Azure Blob Storage."""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Optional

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

logger = logging.getLogger(__name__)

# Azure Storage configuration from environment
AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "")
AZURE_STORAGE_BLOB_ENDPOINT = os.getenv("AZURE_STORAGE_BLOB_ENDPOINT", "")
AZURE_TRANSCRIPTS_CONTAINER = os.getenv("AZURE_TRANSCRIPTS_CONTAINER", "transcripts")
AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID = os.getenv("AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID", "")

# Folder prefixes within the transcripts container
CLINICIAN_NOTES_FOLDER = "clinician-notes"
CYBERSECURITY_AGENT_FOLDER = "cybersecurity-support-agent"
RECAP_FOLDER = "recap"


class TranscriptionStorage:
    """Handles saving conversation transcriptions to Azure Blob Storage."""

    def __init__(self, storage_type: str = "cybersecurity_agent"):
        """Initialize the transcription storage handler.
        
        Args:
            storage_type: Type of storage folder to use.
                         "clinician_notes" - for clinician notes transcription
                         "cybersecurity_agent" - for cybersecurity support agent calls
        """
        self.storage_type = storage_type
        self.conversation_id = str(uuid.uuid4())
        self.client_id = self._generate_client_id()
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.messages: list[dict] = []
        # URL of the most recently saved blob (set by save())
        self.last_saved_url: Optional[str] = None
        
        # Determine storage folder prefix
        if storage_type == "clinician_notes":
            self.folder_prefix = CLINICIAN_NOTES_FOLDER
        elif storage_type == "recap":
            self.folder_prefix = RECAP_FOLDER
        else:
            self.folder_prefix = CYBERSECURITY_AGENT_FOLDER
        
        # Initialize blob service client
        self._blob_service_client: Optional[BlobServiceClient] = None
        self._init_blob_client()
        
        logger.info(f"[TranscriptionStorage] Initialized for {storage_type}, ConversationId: {self.conversation_id}")

    def _generate_client_id(self) -> str:
        """Generate a unique client ID."""
        import random
        return str(random.randint(10000, 99999))

    def _init_blob_client(self):
        """Initialize the Azure Blob Storage client."""
        try:
            if not AZURE_STORAGE_ACCOUNT_NAME and not AZURE_STORAGE_BLOB_ENDPOINT:
                logger.warning("[TranscriptionStorage] Azure Storage not configured, transcriptions will not be saved")
                return
            
            # Determine the blob endpoint
            if AZURE_STORAGE_BLOB_ENDPOINT:
                blob_endpoint = AZURE_STORAGE_BLOB_ENDPOINT
            else:
                blob_endpoint = f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net"
            
            # Use managed identity if client ID is provided, otherwise use DefaultAzureCredential
            if AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID:
                credential = ManagedIdentityCredential(client_id=AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID)
                logger.info("[TranscriptionStorage] Using managed identity authentication")
            else:
                credential = DefaultAzureCredential()
                logger.info("[TranscriptionStorage] Using default Azure credential")
            
            self._blob_service_client = BlobServiceClient(
                account_url=blob_endpoint,
                credential=credential
            )
            logger.info(f"[TranscriptionStorage] Blob client initialized for {blob_endpoint}")
            
        except Exception as e:
            logger.error(f"[TranscriptionStorage] Failed to initialize blob client: {e}")
            self._blob_service_client = None

    def start_conversation(self, client_id: Optional[str] = None):
        """Mark the start of a conversation.
        
        Args:
            client_id: Optional custom client ID. If not provided, uses generated ID.
        """
        self.start_time = datetime.now()
        if client_id:
            self.client_id = client_id
        logger.info(f"[TranscriptionStorage] Conversation started at {self.start_time}")

    def add_user_message(self, text: str):
        """Add a user (customer) message to the transcription.
        
        Args:
            text: The transcribed text from the user
        """
        if text and text.strip():
            self.messages.append({
                "role": "Customer",
                "text": text.strip(),
                "timestamp": datetime.now().isoformat()
            })
            logger.info(f"[TranscriptionStorage] Added user message: {text[:50]}...")

    def add_agent_message(self, text: str, agent_name: Optional[str] = None):
        """Add an agent (AI) message to the transcription.
        
        Args:
            text: The agent's response text
            agent_name: Optional name of the agent (e.g., "Sarah", "Mike")
        """
        if text and text.strip():
            role = f"Agent ({agent_name})" if agent_name else "Agent"
            self.messages.append({
                "role": role,
                "text": text.strip(),
                "timestamp": datetime.now().isoformat()
            })
            logger.info(f"[TranscriptionStorage] Added agent message: {text[:50]}...")

    def add_speaker_message(self, speaker: str, text: str):
        """Add a diarized speaker message to the transcription.

        Used by real-time diarization (ConversationTranscriber) where each
        utterance is attributed to a speaker label like "Speaker 1".
        """
        if text and text.strip():
            role = speaker.strip() if speaker and speaker.strip() else "Unknown"
            self.messages.append({
                "role": role,
                "text": text.strip(),
                "timestamp": datetime.now().isoformat()
            })
            logger.info(f"[TranscriptionStorage] Added [{role}] message: {text[:50]}...")

    def end_conversation(self):
        """Mark the end of the conversation and save the transcription."""
        self.end_time = datetime.now()
        logger.info(f"[TranscriptionStorage] Conversation ended at {self.end_time}")
        self.save()

    def _format_content(self) -> str:
        """Format all messages into a single content string.
        
        Returns:
            Formatted conversation content with role prefixes
        """
        lines = []
        for msg in self.messages:
            role = msg["role"]
            text = msg["text"]
            # Format: "Agent: Hello..." or "Customer (Susan): Hi..."
            lines.append(f"{role}: {text}")
        return "\n\n".join(lines)

    def _format_datetime(self, dt: Optional[datetime]) -> str:
        """Format datetime for JSON output.
        
        Args:
            dt: Datetime to format
            
        Returns:
            Formatted datetime string
        """
        if dt is None:
            dt = datetime.now()
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def save(self) -> Optional[str]:
        """Save the transcription to Azure Blob Storage.
        
        Returns:
            URL of the saved blob, or None if save failed
        """
        if not self._blob_service_client:
            logger.warning("[TranscriptionStorage] Blob client not initialized, skipping save")
            return None

        if not self.messages:
            logger.info("[TranscriptionStorage] No messages captured, skipping save")
            return None

        try:
            # Build transcription data
            transcription_data = {
                "ClientId": self.client_id,
                "ConversationId": self.conversation_id,
                "StartTime": self._format_datetime(self.start_time),
                "EndTime": self._format_datetime(self.end_time),
                "Messages": self.messages,
                "Content": self._format_content()
            }

            # Generate blob name with date and time prefix in filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            blob_name = f"{self.folder_prefix}/transcription_{timestamp}_{self.conversation_id[:8]}.json"
            
            # Get container client
            container_client = self._blob_service_client.get_container_client(AZURE_TRANSCRIPTS_CONTAINER)
            
            # Upload to blob storage
            blob_client = container_client.get_blob_client(blob_name)
            json_content = json.dumps(transcription_data, indent=4, ensure_ascii=False)
            
            blob_client.upload_blob(
                json_content,
                overwrite=True,
                content_settings=ContentSettings(content_type="application/json")
            )
            
            blob_url = blob_client.url
            self.last_saved_url = blob_url
            logger.info(f"[TranscriptionStorage] Transcription saved to: {blob_url}")
            return blob_url
            
        except Exception as e:
            logger.error(f"[TranscriptionStorage] Failed to save transcription: {e}")
            return None

    def get_transcription_data(self) -> dict:
        """Get the current transcription data as a dictionary.
        
        Returns:
            Dictionary with transcription data
        """
        return {
            "ClientId": self.client_id,
            "ConversationId": self.conversation_id,
            "StartTime": self._format_datetime(self.start_time),
            "EndTime": self._format_datetime(self.end_time or datetime.now()),
            "Content": self._format_content()
        }
