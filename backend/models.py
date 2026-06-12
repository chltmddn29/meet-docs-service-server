from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    settings = Column(Text, nullable=True)
    
    meetings = relationship("Meeting", back_populates="creator")


class Meeting(Base):
    __tablename__ = "meetings"
    
    meeting_id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    meeting_date = Column(DateTime, nullable=True)
    duration = Column(Integer, nullable=True)
    status = Column(String, default="recording")
    tags = Column(Text, nullable=True)
    
    creator = relationship("User", back_populates="meetings")
    transcripts = relationship("Transcript", back_populates="meeting")
    agenda_items = relationship("MeetingAgendaItem", back_populates="meeting")
    platform_saves = relationship("PlatformSave", back_populates="meeting")


class Transcript(Base):
    __tablename__ = "transcripts"
    
    transcript_id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id"))
    audio_file_path = Column(String)
    raw_text = Column(Text, nullable=True)
    processed_text = Column(Text, nullable=True)
    generated_at = Column(DateTime, default=datetime.utcnow)
    
    meeting = relationship("Meeting", back_populates="transcripts")


class MeetingAgendaItem(Base):
    __tablename__ = "meeting_agenda_items"
    
    item_id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id"))
    agenda = Column(String)
    order = Column(Integer)
    content = Column(Text, nullable=True)
    decision = Column(Text, nullable=True)
    action_items = Column(Text, nullable=True)
    participant = Column(String, nullable=True)
    
    meeting = relationship("Meeting", back_populates="agenda_items")


class PlatformSave(Base):
    __tablename__ = "platform_saves"
    
    save_id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meetings.meeting_id"))
    platform = Column(String)
    save_status = Column(String)
    platform_doc_id = Column(String, nullable=True)
    saved_at = Column(DateTime, default=datetime.utcnow)
    error_message = Column(Text, nullable=True)
    
    meeting = relationship("Meeting", back_populates="platform_saves")