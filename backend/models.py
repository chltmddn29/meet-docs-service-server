from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, LargeBinary
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
    participants = Column(Text, nullable=True)
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
    # 오디오 바이트를 DB에 보관 → Render 무료 디스크가 휘발성이어도 재시작 후 복원 가능
    audio_data = Column(LargeBinary, nullable=True)
    audio_filename = Column(String, nullable=True)
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
    participants = Column(Text, nullable=True)
    
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


class Template(Base):
    __tablename__ = "templates"

    template_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(Text, nullable=True)
    agenda_items = Column(Text, nullable=True)   # JSON 배열 문자열로 저장
    participants = Column(Text, nullable=True)   # 쉼표 구분 문자열
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class FormatTemplate(Base):
    """업로드한 샘플 문서(서식). AI가 이 형식대로 회의록을 생성하는 데 사용."""
    __tablename__ = "format_templates"

    format_template_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    content = Column(Text)                        # 업로드 파일에서 추출한 본문(서식 예시)
    source_filename = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.user_id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)