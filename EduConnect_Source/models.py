from EduConnect_Source.extensions import db
from datetime import datetime, timezone, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import Index, func
import secrets
import string

# Indian Standard Time (IST) - UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

def get_ist_now():
    """Get current time in Indian Standard Time"""
    return datetime.now(IST).replace(tzinfo=None)  # Remove timezone info for database compatibility

class User(db.Model):
    """User model for students, teachers, parents, and admins"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    bio = db.Column(db.Text, default='')
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    is_teacher = db.Column(db.Boolean, default=False, nullable=False)
    is_prefect = db.Column(db.Boolean, default=False, nullable=False)
    is_parent = db.Column(db.Boolean, default=False, nullable=False)  # New field for parents
    house = db.Column(db.String(20), nullable=True)  # St. Patrick, St. Raphael, St. Nicolas, St. Michael
    grade_level = db.Column(db.String(20))
    section = db.Column(db.String(1), nullable=True)  # A-I sections for both students and teachers
    subject_taught = db.Column(db.String(50), nullable=True)  # For teachers
    class_teacher_grade = db.Column(db.String(20), nullable=True)  # Grade this teacher is class teacher of
    class_teacher_section = db.Column(db.String(1), nullable=True)  # Section this teacher is class teacher of
    points = db.Column(db.Integer, default=0, nullable=False)  # Student points system - default 12
    is_limited = db.Column(db.Boolean, default=False, nullable=False)  # Limitation status
    limit_reason = db.Column(db.Text)  # Reason for limitation
    limit_start_date = db.Column(db.DateTime)  # When limitation started
    limit_end_date = db.Column(db.DateTime)  # When limitation expires (None for permanent)
    limited_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # Admin who applied limitation
    date_created = db.Column(db.DateTime, default=get_ist_now)
    
    # Relationships - optimized with selectinload for performance
    posts = db.relationship('Post', backref='author', lazy='select', cascade='all, delete-orphan')
    comments = db.relationship('Comment', backref='author', lazy='select', cascade='all, delete-orphan')
    likes = db.relationship('Like', backref='user', lazy='select', cascade='all, delete-orphan')
    
    # DM relationships
    sent_messages = db.relationship('DirectMessage', foreign_keys='DirectMessage.sender_id', backref='sender', lazy='select', cascade='all, delete-orphan')
    received_messages = db.relationship('DirectMessage', foreign_keys='DirectMessage.receiver_id', backref='receiver', lazy='select', cascade='all, delete-orphan')
    
    # Group relationships
    created_groups = db.relationship('Group', foreign_keys='Group.admin_id', backref='admin', lazy='select')
    group_memberships = db.relationship('GroupMembership', backref='user', lazy='select', cascade='all, delete-orphan')
    
    # Parent-child relationships
    children_relations = db.relationship('ParentChild', foreign_keys='ParentChild.parent_id', backref='parent', lazy='select', cascade='all, delete-orphan')
    parent_relations = db.relationship('ParentChild', foreign_keys='ParentChild.child_id', backref='child', lazy='select', cascade='all, delete-orphan')
    
    # Limitation relationship
    limited_by = db.relationship('User', foreign_keys=[limited_by_id], remote_side=[id], backref='users_limited')
    
    def set_password(self, password):
        """Set password hash"""
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """Check if provided password matches hash"""
        return check_password_hash(self.password_hash, password)
    
    def get_groups(self):
        """Get all groups this user is a member of - optimized with join"""
        from sqlalchemy.orm import joinedload
        memberships = GroupMembership.query.filter_by(user_id=self.id).options(joinedload(GroupMembership.group)).all()
        return [membership.group for membership in memberships]
    
    @property
    def unread_message_count(self):
        """Cached property for unread message count"""
        return DirectMessage.query.filter_by(receiver_id=self.id, is_read=False).count()
    
    @property
    def post_count(self):
        """Cached property for post count"""
        return Post.query.filter_by(user_id=self.id).count()
    
    @property
    def sent_message_count(self):
        """Cached property for sent message count"""
        return DirectMessage.query.filter_by(sender_id=self.id).count()
    
    @property
    def group_count(self):
        """Cached property for group membership count"""
        return GroupMembership.query.filter_by(user_id=self.id).count()
    
    def can_delete_posts(self):
        """Check if user can delete posts (admin, teacher, or prefect)"""
        return self.is_admin or self.is_teacher or self.is_prefect
    
    def can_delete_post(self, post):
        """Check if user can delete a specific post"""
        # Users can delete their own posts
        if post.user_id == self.id:
            return True
        
        # Use hierarchy-based deletion rules
        return self.can_report_or_delete_user(post.author)
    
    @property
    def role_display(self):
        """Get user role for display purposes"""
        if self.is_admin:
            return "Admin"
        elif self.is_teacher:
            return f"Teacher - {self.subject_taught}" if self.subject_taught else "Teacher"
        elif self.is_parent:
            children_count = len(self.get_children())
            return f"Parent ({children_count} child{'ren' if children_count != 1 else ''})"
        elif self.is_prefect:
            return f"Prefect - {self.house}" if self.house else "Prefect"
        else:
            return "Student"
    
    def get_children(self):
        """Get all children for parent accounts"""
        if not self.is_parent:
            return []
        return [relation.child for relation in self.children_relations]
    
    def get_parents(self):
        """Get all parents for student accounts"""
        return [relation.parent for relation in self.parent_relations]
    
    def get_grade_numeric(self):
        """Convert grade level to numeric for comparison"""
        if not self.grade_level:
            return 0
        try:
            # Extract numeric part from grade (e.g., "Grade 9" -> 9, "9" -> 9)
            grade_str = self.grade_level.replace('Grade ', '').replace('grade ', '').strip()
            return int(grade_str)
        except (ValueError, AttributeError):
            return 0
    
    def can_report_or_delete_user(self, target_user):
        """Check if this user can report or delete content from target_user based on hierarchy"""
        # Admins can manage everyone
        if self.is_admin:
            return True
        
        # Teachers can manage all students and other teachers
        if self.is_teacher:
            # Teachers cannot target admins
            if target_user.is_admin:
                return False
            return True
        
        # Prefects cannot target admins or teachers (only students)
        if self.is_prefect:
            if target_user.is_admin or target_user.is_teacher:
                return False
            # Only students can be targeted by prefects
            if not target_user.is_student():
                return False
            return True
        
        return False
    
    def is_student(self):
        """Check if user is a student (not admin, teacher, or parent)"""
        return not (self.is_admin or self.is_teacher or self.is_parent)
    
    def is_limited_currently(self):
        """Check if user is currently limited"""
        if not self.is_limited:
            return False
        if self.limit_end_date and get_ist_now() > self.limit_end_date:
            # Limitation has expired, update status
            self.is_limited = False
            self.limit_reason = None
            self.limit_start_date = None
            self.limit_end_date = None
            self.limited_by_id = None
            db.session.commit()
            return False
        return True
    
    def limit_user(self, reason, duration_hours=None, limited_by_admin=None):
        """Limit the user with optional duration"""
        self.is_limited = True
        self.limit_reason = reason
        self.limit_start_date = get_ist_now()
        self.limited_by_id = limited_by_admin.id if limited_by_admin else None
        
        if duration_hours:
            from datetime import timedelta
            self.limit_end_date = get_ist_now() + timedelta(hours=duration_hours)
        else:
            self.limit_end_date = None  # Permanent limitation
        
        db.session.commit()
    
    def unlimit_user(self):
        """Remove limitation from user"""
        self.is_limited = False
        self.limit_reason = None
        self.limit_start_date = None
        self.limit_end_date = None
        self.limited_by_id = None
        db.session.commit()
    
    def can_monitor(self, student):
        """Check if this parent can monitor the given student"""
        if not self.is_parent:
            return False
        return any(relation.child_id == student.id for relation in self.children_relations)
    
    def get_class_teacher(self):
        """Get the class teacher for this student"""
        if self.is_teacher or self.is_admin or self.is_parent:
            return None
        if not self.grade_level or not self.section:
            return None
        
        # Find teacher who is class teacher of this grade and section
        class_teacher = User.query.filter_by(
            is_teacher=True,
            class_teacher_grade=self.grade_level,
            class_teacher_section=self.section
        ).first()
        
        return class_teacher
    
    def update_bio_with_class_teacher(self):
        """Update student bio with class teacher information"""
        if self.is_teacher or self.is_admin or self.is_parent:
            return
        
        class_teacher = self.get_class_teacher()
        if class_teacher:
            # Update bio to include class teacher info if not already present
            class_teacher_info = f"Class Teacher: {class_teacher.full_name}"
            if class_teacher_info not in self.bio:
                if self.bio and not self.bio.endswith('\n'):
                    self.bio += '\n'
                self.bio += class_teacher_info
    
    def __repr__(self):
        return f'<User {self.username}>'

class Post(db.Model):
    """Post model for student posts"""
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=get_ist_now)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships - optimized loading
    comments = db.relationship('Comment', backref='post', lazy='select', cascade='all, delete-orphan')
    likes = db.relationship('Like', backref='post', lazy='select', cascade='all, delete-orphan')
    
    @property
    def like_count(self):
        """Get total number of likes for this post - cached"""
        return Like.query.filter_by(post_id=self.id).count()
    
    def is_liked_by(self, user):
        """Check if post is liked by specific user - optimized"""
        return Like.query.filter_by(user_id=user.id, post_id=self.id).first() is not None
    
    @property
    def comment_count(self):
        """Get total number of comments for this post"""
        return Comment.query.filter_by(post_id=self.id).count()
    
    def __repr__(self):
        return f'<Post {self.id} by {self.author.username}>'

class Comment(db.Model):
    """Comment model for post comments"""
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=get_ist_now)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    
    def __repr__(self):
        return f'<Comment {self.id} by {self.author.username}>'

class Like(db.Model):
    """Like model for post likes"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    date_liked = db.Column(db.DateTime, default=get_ist_now)
    
    # Ensure unique constraint - one like per user per post
    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='unique_user_post_like'),)
    
    def __repr__(self):
        return f'<Like {self.user.username} on post {self.post_id}>'

class DirectMessage(db.Model):
    """Direct message model for private conversations"""
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_sent = db.Column(db.DateTime, default=get_ist_now)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    is_edited = db.Column(db.Boolean, default=False, nullable=False)
    date_edited = db.Column(db.DateTime)
    
    def __repr__(self):
        return f'<DirectMessage from {self.sender.username} to {self.receiver.username}>'

class Group(db.Model):
    """Group model for student groups"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, default='')
    is_private = db.Column(db.Boolean, default=False, nullable=False)
    invite_code = db.Column(db.String(20), unique=True, nullable=True)
    date_created = db.Column(db.DateTime, default=get_ist_now)
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships - optimized loading
    memberships = db.relationship('GroupMembership', backref='group', lazy='select', cascade='all, delete-orphan')
    
    @staticmethod
    def generate_invite_code():
        """Generate a unique 8-character invite code"""
        while True:
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            if not Group.query.filter_by(invite_code=code).first():
                return code
    
    @property
    def member_count(self):
        """Get the number of members in this group - cached"""
        return GroupMembership.query.filter_by(group_id=self.id).count()
    
    def is_member(self, user):
        """Check if user is a member of this group - optimized"""
        return GroupMembership.query.filter_by(user_id=user.id, group_id=self.id).first() is not None
    
    @property
    def message_count(self):
        """Get the number of messages in this group"""
        return GroupMessage.query.filter_by(group_id=self.id).count()
    
    def __repr__(self):
        return f'<Group {self.name}>'
    
    def get_member_count(self):
        """Get the number of members in this group"""
        return GroupMembership.query.filter_by(group_id=self.id).count()
    
    def is_member(self, user):
        """Check if user is a member of this group"""
        if not user:
            return False
        return GroupMembership.query.filter_by(group_id=self.id, user_id=user.id).first() is not None

class GroupMembership(db.Model):
    """Association table for group memberships"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    date_joined = db.Column(db.DateTime, default=get_ist_now)
    
    # Ensure unique constraint - one membership per user per group
    __table_args__ = (db.UniqueConstraint('user_id', 'group_id', name='unique_user_group_membership'),)
    
    def __repr__(self):
        return f'<GroupMembership {self.user.username} in {self.group.name}>'

class GroupMessage(db.Model):
    """Group message model for group chat"""
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_sent = db.Column(db.DateTime, default=get_ist_now)
    is_edited = db.Column(db.Boolean, default=False, nullable=False)
    date_edited = db.Column(db.DateTime)
    
    # Relationships - optimized loading
    group = db.relationship('Group', backref='messages', lazy='select')
    user = db.relationship('User', backref='sent_group_messages', lazy='select')
    
    def __repr__(self):
        return f'<GroupMessage in {self.group.name} by {self.user.username}>'

class LostAndFound(db.Model):
    """Lost and Found items model"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    location_lost = db.Column(db.String(100), nullable=False)
    date_lost = db.Column(db.Date, nullable=False)
    date_reported = db.Column(db.DateTime, default=get_ist_now)
    is_found = db.Column(db.Boolean, default=False)
    found_location = db.Column(db.String(100))
    found_date = db.Column(db.Date)
    found_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    contact_info = db.Column(db.String(200))
    
    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref='lost_items')
    found_by_admin = db.relationship('User', foreign_keys=[found_by_admin_id])
    
    def __repr__(self):
        return f'<LostAndFound {self.item_name} by {self.user.username}>'

class Classwork(db.Model):
    """Model for teacher classwork uploads (PDFs)"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    file_size = db.Column(db.Integer)  # Size in bytes
    upload_date = db.Column(db.DateTime, default=get_ist_now)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)  # Hindi, Maths, Science, etc.
    target_grades = db.Column(db.Text)  # JSON string of selected grades
    target_sections = db.Column(db.Text)  # JSON string of selected sections
    
    teacher = db.relationship('User', backref=db.backref('classworks', lazy='dynamic'))
    
    def __repr__(self):
        return f'<Classwork {self.title}>'
    
    @property
    def file_size_mb(self):
        """Get file size in MB"""
        if self.file_size:
            return round(self.file_size / (1024 * 1024), 2)
        return 0
    
    def get_target_grades(self):
        """Get list of target grades"""
        if self.target_grades:
            import json
            return json.loads(self.target_grades)
        return []
    
    def get_target_sections(self):
        """Get list of target sections"""
        if self.target_sections:
            import json
            return json.loads(self.target_sections)
        return []
    
    def is_visible_to_student(self, student):
        """Check if this classwork is visible to a specific student"""
        target_grades = self.get_target_grades()
        target_sections = self.get_target_sections()
        
        # If no specific targeting, visible to all
        if not target_grades:
            return True
            
        # Check if student's grade and section match
        student_grade = student.grade_level
        student_section = student.section
        
        # Normalize grade comparison - handle both "Grade X" and "X" formats
        for grade in target_grades:
            # Extract numeric part from target grade (e.g., "Grade 8" -> "8")
            target_grade_num = grade.replace('Grade ', '').replace('grade ', '').strip()
            # Extract numeric part from student grade (handle both formats)
            student_grade_num = str(student_grade).replace('Grade ', '').replace('grade ', '').strip() if student_grade else ''
            
            if target_grade_num == student_grade_num:
                # If no specific sections, visible to all sections of this grade
                if not target_sections:
                    return True
                # Check if student's section is in target sections
                if student_section in target_sections:
                    return True
        
        return False

class Homework(db.Model):
    """Model for teacher homework assignments"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    subject = db.Column(db.String(100), nullable=False)  # Hindi, Maths, Science, etc.
    due_date = db.Column(db.DateTime, nullable=False)
    assigned_date = db.Column(db.DateTime, default=get_ist_now)
    teacher_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_grades = db.Column(db.Text)  # JSON string of selected grades
    target_sections = db.Column(db.Text)  # JSON string of selected sections
    
    # Optional attachment
    attachment_filename = db.Column(db.String(255))
    attachment_path = db.Column(db.String(500))
    attachment_size = db.Column(db.Integer)
    
    teacher = db.relationship('User', backref=db.backref('homework_assignments', lazy='dynamic'))
    
    def __repr__(self):
        return f'<Homework {self.title}>'
    
    @property
    def is_overdue(self):
        """Check if homework is overdue"""
        return get_ist_now() > self.due_date
    
    @property
    def attachment_size_mb(self):
        """Get attachment size in MB"""
        if self.attachment_size:
            return round(self.attachment_size / (1024 * 1024), 2)
        return 0
    
    def get_target_grades(self):
        """Get list of target grades"""
        if self.target_grades:
            import json
            return json.loads(self.target_grades)
        return []
    
    def get_target_sections(self):
        """Get list of target sections"""
        if self.target_sections:
            import json
            return json.loads(self.target_sections)
        return []
    
    def is_visible_to_student(self, student):
        """Check if this homework is visible to a specific student"""
        target_grades = self.get_target_grades()
        target_sections = self.get_target_sections()
        
        # If no specific targeting, visible to all
        if not target_grades:
            return True
            
        # Check if student's grade and section match
        student_grade = student.grade_level
        student_section = student.section
        
        # Normalize grade comparison - handle both "Grade X" and "X" formats
        for grade in target_grades:
            # Extract numeric part from target grade (e.g., "Grade 8" -> "8")
            target_grade_num = grade.replace('Grade ', '').replace('grade ', '').strip()
            # Extract numeric part from student grade (handle both formats)
            student_grade_num = str(student_grade).replace('Grade ', '').replace('grade ', '').strip() if student_grade else ''
            
            if target_grade_num == student_grade_num:
                # If no specific sections, visible to all sections of this grade
                if not target_sections:
                    return True
                # Check if student's section is in target sections
                if student_section in target_sections:
                    return True
        
        return False

class Circular(db.Model):
    """Model for school circulars/announcements"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=False)  # Holiday, Event, Reminder, General
    priority = db.Column(db.String(20), default='Normal')  # High, Normal, Low
    date_created = db.Column(db.DateTime, default=get_ist_now)
    date_published = db.Column(db.DateTime, default=get_ist_now)
    expires_on = db.Column(db.DateTime)  # Optional expiry date
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    
    # Optional attachment
    attachment_filename = db.Column(db.String(255))
    attachment_path = db.Column(db.String(500))
    attachment_size = db.Column(db.Integer)
    
    created_by = db.relationship('User', backref=db.backref('circulars', lazy='dynamic'))
    
    def __repr__(self):
        return f'<Circular {self.title}>'
    
    @property
    def is_expired(self):
        """Check if circular is expired"""
        if self.expires_on:
            return get_ist_now() > self.expires_on
        return False
    
    @property
    def attachment_size_mb(self):
        """Get attachment size in MB"""
        if self.attachment_size:
            return round(self.attachment_size / (1024 * 1024), 2)
        return 0
    
    @property
    def days_since_published(self):
        """Get days since publication"""
        return (get_ist_now() - self.date_published).days

class ParentChild(db.Model):
    """Model to manage parent-child relationships"""
    id = db.Column(db.Integer, primary_key=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    child_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date_created = db.Column(db.DateTime, default=get_ist_now)
    
    # Ensure a parent-child relationship is unique
    __table_args__ = (db.UniqueConstraint('parent_id', 'child_id', name='unique_parent_child'),)
    
    def __repr__(self):
        return f'<ParentChild parent_id={self.parent_id} child_id={self.child_id}>'

class CalendarEvent(db.Model):
    """Model for monthly calendar events that admin can edit"""
    id = db.Column(db.Integer, primary_key=True)
    month_name = db.Column(db.String(20), nullable=False)  # January, February, etc.
    year = db.Column(db.Integer, nullable=False)
    day_number = db.Column(db.Integer, nullable=False)  # 1-31
    event_text = db.Column(db.String(200), default='')  # Event description for that day
    last_updated = db.Column(db.DateTime, default=get_ist_now)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Relationships
    updated_by = db.relationship('User', backref='calendar_updates')
    
    # Unique constraint for month, year, day combination
    __table_args__ = (db.UniqueConstraint('month_name', 'year', 'day_number', name='unique_calendar_entry'),)
    
    @staticmethod
    def get_days_in_month(month_name):
        """Get number of days in a month - February always returns 29"""
        days_map = {
            'January': 31, 'February': 29, 'March': 31, 'April': 30,
            'May': 31, 'June': 30, 'July': 31, 'August': 31,
            'September': 30, 'October': 31, 'November': 30, 'December': 31
        }
        return days_map.get(month_name, 30)
    
    @staticmethod
    def get_month_calendar(month_name, year):
        """Get all calendar events for a specific month and year"""
        events = CalendarEvent.query.filter_by(month_name=month_name, year=year).all()
        return {event.day_number: event.event_text for event in events}
    
    def __repr__(self):
        return f'<CalendarEvent {self.month_name} {self.day_number}, {self.year}>'

class Announcement(db.Model):
    """Model for admin announcements with photo attachments"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_created = db.Column(db.DateTime, default=get_ist_now)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    
    # Photo attachment (only images allowed)
    photo_filename = db.Column(db.String(255))
    photo_path = db.Column(db.String(500))
    photo_size = db.Column(db.Integer)
    
    # Relationships
    created_by = db.relationship('User', backref='announcements')
    
    @property
    def photo_size_mb(self):
        """Get photo size in MB"""
        if self.photo_size:
            return round(self.photo_size / (1024 * 1024), 2)
        return 0
    
    def __repr__(self):
        return f'<Announcement {self.title}>'

class DailyPostingActivity(db.Model):
    """Model to track daily posting activity for real analytics"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    post_count = db.Column(db.Integer, default=1)
    last_updated = db.Column(db.DateTime, default=get_ist_now)
    
    # Relationships
    user = db.relationship('User', backref='daily_activities')
    
    # Unique constraint to prevent duplicate entries for same user and date
    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='unique_user_date_activity'),)
    
    @staticmethod
    def record_post(user_id):
        """Record a post for today's date"""
        today = get_ist_now().date()
        
        # Check if record exists for today
        activity = DailyPostingActivity.query.filter_by(
            user_id=user_id, 
            date=today
        ).first()
        
        if activity:
            # Increment count
            activity.post_count += 1
            activity.last_updated = get_ist_now()
        else:
            # Create new record
            activity = DailyPostingActivity(
                user_id=user_id,
                date=today,
                post_count=1
            )
            db.session.add(activity)
        
        db.session.commit()
        return activity
    
    @staticmethod
    def get_user_activity_last_30_days(user_id):
        """Get user's posting activity for the last 30 days"""
        from datetime import date, timedelta
        
        end_date = date.today()
        start_date = end_date - timedelta(days=29)  # 30 days total
        
        activities = DailyPostingActivity.query.filter(
            DailyPostingActivity.user_id == user_id,
            DailyPostingActivity.date.between(start_date, end_date)
        ).all()
        
        # Create a dictionary for easy lookup
        activity_dict = {activity.date: activity.post_count for activity in activities}
        
        # Generate all 30 days with counts (0 if no posts)
        result = []
        current_date = start_date
        while current_date <= end_date:
            result.append({
                'date': current_date,
                'count': activity_dict.get(current_date, 0)
            })
            current_date += timedelta(days=1)
        
        return result
    
    def __repr__(self):
        return f'<DailyPostingActivity user_id={self.user_id} date={self.date} count={self.post_count}>'

class Report(db.Model):
    """Model for reporting inappropriate content (posts, messages, group messages)"""
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reported_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content_type = db.Column(db.String(20), nullable=False)  # 'post', 'direct_message', 'group_message'
    content_id = db.Column(db.Integer, nullable=False)  # ID of the reported content
    justification = db.Column(db.Text, nullable=False)  # Reason for reporting
    status = db.Column(db.String(20), default='pending', nullable=False)  # 'pending', 'approved', 'rejected'
    date_reported = db.Column(db.DateTime, default=get_ist_now)
    date_reviewed = db.Column(db.DateTime)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    admin_notes = db.Column(db.Text)  # Optional notes from admin
    points_adjustment = db.Column(db.Integer, default=0)  # Points change applied (negative for deduction)
    
    # Relationships
    reporter = db.relationship('User', foreign_keys=[reporter_id], backref='reports_made')
    reported_user = db.relationship('User', foreign_keys=[reported_user_id], backref='reports_against')
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id], backref='reports_reviewed')
    
    def get_content(self):
        """Get the actual content object that was reported"""
        if self.content_type == 'post':
            return Post.query.get(self.content_id)
        elif self.content_type == 'direct_message':
            return DirectMessage.query.get(self.content_id)
        elif self.content_type == 'group_message':
            return GroupMessage.query.get(self.content_id)
        return None
    
    def get_content_preview(self):
        """Get a preview of the reported content"""
        content = self.get_content()
        if content and hasattr(content, 'content'):
            text = content.content
            return text[:100] + '...' if len(text) > 100 else text
        return "Content not found"
    
    def __repr__(self):
        return f'<Report {self.id} - {self.content_type} by {self.reported_user.username}>'

# Database Indexes for Performance Optimization
Index('idx_user_username', User.username)
Index('idx_user_email', User.email)
Index('idx_user_is_admin', User.is_admin)
Index('idx_user_is_prefect', User.is_prefect)
Index('idx_user_date_created', User.date_created)

Index('idx_post_user_id', Post.user_id)
Index('idx_post_date_posted', Post.date_posted)

Index('idx_comment_post_id', Comment.post_id)
Index('idx_comment_user_id', Comment.user_id)
Index('idx_comment_date_posted', Comment.date_posted)

Index('idx_like_user_post', Like.user_id, Like.post_id)
Index('idx_like_post_id', Like.post_id)

Index('idx_direct_message_sender', DirectMessage.sender_id)
Index('idx_direct_message_receiver', DirectMessage.receiver_id)
Index('idx_direct_message_is_read', DirectMessage.is_read)
Index('idx_direct_message_date_sent', DirectMessage.date_sent)

Index('idx_group_is_private', Group.is_private)
Index('idx_group_invite_code', Group.invite_code)
Index('idx_group_admin_id', Group.admin_id)
Index('idx_group_date_created', Group.date_created)

Index('idx_group_membership_user_group', GroupMembership.user_id, GroupMembership.group_id)
Index('idx_group_membership_group_id', GroupMembership.group_id)

Index('idx_group_message_group_id', GroupMessage.group_id)
Index('idx_group_message_user_id', GroupMessage.user_id)
Index('idx_group_message_date_sent', GroupMessage.date_sent)

Index('idx_lost_found_user_id', LostAndFound.user_id)
Index('idx_lost_found_is_found', LostAndFound.is_found)
Index('idx_lost_found_date_reported', LostAndFound.date_reported)

Index('idx_classwork_teacher_id', Classwork.teacher_id)
Index('idx_classwork_upload_date', Classwork.upload_date)
Index('idx_classwork_subject', Classwork.subject)

Index('idx_homework_teacher_id', Homework.teacher_id)
Index('idx_homework_due_date', Homework.due_date)
Index('idx_homework_assigned_date', Homework.assigned_date)
Index('idx_homework_subject', Homework.subject)

Index('idx_circular_created_by', Circular.created_by_id)
Index('idx_circular_date_published', Circular.date_published)
Index('idx_circular_category', Circular.category)
Index('idx_circular_is_active', Circular.is_active)
Index('idx_circular_expires_on', Circular.expires_on)

Index('idx_parent_child_parent_id', ParentChild.parent_id)
Index('idx_parent_child_child_id', ParentChild.child_id)

Index('idx_daily_activity_user_date', DailyPostingActivity.user_id, DailyPostingActivity.date)
Index('idx_daily_activity_date', DailyPostingActivity.date)

Index('idx_calendar_event_month_year', CalendarEvent.month_name, CalendarEvent.year)
Index('idx_calendar_event_updated_by', CalendarEvent.updated_by_id)

Index('idx_announcement_created_by', Announcement.created_by_id)
Index('idx_announcement_date_created', Announcement.date_created)
Index('idx_announcement_is_active', Announcement.is_active)

Index('idx_report_reporter_id', Report.reporter_id)
Index('idx_report_reported_user_id', Report.reported_user_id)
Index('idx_report_status', Report.status)
Index('idx_report_date_reported', Report.date_reported)
Index('idx_report_content_type', Report.content_type)
