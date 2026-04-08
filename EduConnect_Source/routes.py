from flask import render_template, request, redirect, url_for, flash, session, jsonify
from flask import current_app
from EduConnect_Source.extensions import db
from EduConnect_Source.models import (
    User, Post, Comment, Like, DirectMessage, Group, GroupMembership,
    GroupMessage, LostAndFound, Classwork, Homework, Circular,
    ParentChild, DailyPostingActivity, CalendarEvent, Announcement, Report, get_ist_now
)
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import or_, and_, func, case
from sqlalchemy.orm import joinedload, selectinload, subqueryload
from datetime import datetime
import os
import logging
# Make helper functions available to templates
@app.context_processor
def utility_processor():
    def get_user_by_id(user_id):
        if user_id:
            return User.query.get(user_id)
        return None
    
    def is_logged_in():
        return 'user_id' in session
    
    def can_access_social_features():
        """Check if current user can access social features (not limited)"""
        if 'user_id' not in session:
            return False
        user = User.query.get(session['user_id'])
        return user and not user.is_limited_currently()
    
    return dict(get_user_by_id=get_user_by_id, is_logged_in=is_logged_in, can_access_social_features=can_access_social_features, datetime=get_ist_now, get_ist_now=get_ist_now)

@app.route('/health')
def health():
    return jsonify({'status': '200 OK'}), 200

@app.route('/')
def index():
    """Home page - redirect based on login status"""
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            if user.is_admin:
                return redirect(url_for('admin_dashboard'))
            elif user.is_parent:
                return redirect(url_for('parent_dashboard'))
            else:
                return redirect(url_for('student_dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page for both students and admins"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # Limited users can still log in but with restricted access
            # No login restriction for limited users - they'll see restricted navigation
            
            session.permanent = True
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            session['is_teacher'] = user.is_teacher
            session['is_parent'] = user.is_parent
            session['full_name'] = user.full_name
            
            flash(f'Welcome back, {user.full_name}!', 'success')
            
            if user.is_admin:
                return redirect(url_for('admin_dashboard'))
            elif user.is_parent:
                return redirect(url_for('parent_dashboard'))
            else:
                return redirect(url_for('student_dashboard'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout and clear session"""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/admin/dashboard')
def admin_dashboard():
    """Admin dashboard with student list - optimized"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    # Paginated students query
    page = request.args.get('page', 1, type=int)
    per_page = 50  # Limit to 50 students per page
    
    students_pagination = User.query.filter_by(is_admin=False)\
        .order_by(User.full_name)\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    # Use efficient count and joins
    total_posts = Post.query.count()
    
    # Get recent lost items with eager loading
    recent_lost_items = LostAndFound.query\
        .options(joinedload(LostAndFound.user))\
        .order_by(LostAndFound.date_reported.desc())\
        .limit(10).all()
    
    pending_lost_items = LostAndFound.query.filter_by(is_found=False).count()
    
    return render_template('admin_dashboard.html', 
                         students=students_pagination.items,
                         pagination=students_pagination,
                         total_posts=total_posts,
                         recent_lost_items=recent_lost_items,
                         pending_lost_items=pending_lost_items)

@app.route('/admin/create_account', methods=['GET', 'POST'])
def create_account():
    """Create new student or teacher account"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        full_name = request.form['full_name']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        account_type = request.form['account_type']  # 'student' or 'teacher'
        
        # Validate password confirmation
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return redirect(url_for('admin_accounts'))
        
        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'error')
            return redirect(url_for('admin_accounts'))
        
        if User.query.filter_by(email=email).first():
            flash('Email already exists.', 'error')
            return redirect(url_for('admin_accounts'))
        
        # Create new user based on account type
        if account_type == 'teacher':
            subject_taught = request.form.get('subject_taught', '')
            is_class_teacher = 'is_class_teacher' in request.form
            class_teacher_grade = request.form.get('class_teacher_grade', '') if is_class_teacher else None
            class_teacher_section = request.form.get('class_teacher_section', '') if is_class_teacher else None
            
            new_user = User(
                username=username,
                email=email,
                full_name=full_name,
                is_teacher=True,
                subject_taught=subject_taught,
                class_teacher_grade=class_teacher_grade,
                class_teacher_section=class_teacher_section,
                is_admin=False
            )
            success_msg = f'Teacher account created successfully for {full_name}.'
        elif account_type == 'parent':
            new_user = User(
                username=username,
                email=email,
                full_name=full_name,
                is_parent=True,
                is_admin=False
            )
            success_msg = f'Parent account created successfully for {full_name}.'
        else:  # student
            grade_level = request.form.get('grade_level', '')
            section = request.form.get('section', '')
            house = request.form.get('house') or None
            is_prefect = 'is_prefect' in request.form
            
            new_user = User(
                username=username,
                email=email,
                full_name=full_name,
                grade_level=grade_level,
                section=section,
                house=house,
                is_prefect=is_prefect,
                is_admin=False
            )
            success_msg = f'Student account created successfully for {full_name}.'
        
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        
        # Update student bio with class teacher info if applicable
        if account_type == 'student':
            new_user.update_bio_with_class_teacher()
            db.session.commit()
        
        # Handle parent-child relationships if this is a parent account
        if account_type == 'parent':
            children_ids = request.form.getlist('children_ids')
            for child_id in children_ids:
                if child_id:  # Skip empty values
                    try:
                        # Check if child exists and is a student
                        child = User.query.get(int(child_id))
                        if child and not child.is_parent and not child.is_teacher and not child.is_admin:
                            parent_child = ParentChild(
                                parent_id=new_user.id,
                                child_id=child.id
                            )
                            db.session.add(parent_child)
                    except (ValueError, TypeError):
                        pass  # Skip invalid child_id values
            
            db.session.commit()
            children_count = len([c for c in children_ids if c])
            if children_count > 0:
                success_msg += f' Assigned to monitor {children_count} child{"ren" if children_count != 1 else ""}.'
        
        flash(success_msg, 'success')
        return redirect(url_for('admin_accounts'))

# Legacy route for backward compatibility
@app.route('/admin/create_student', methods=['GET', 'POST'])
def create_student():
    """Legacy route - redirects to create_account"""
    return redirect(url_for('create_account'))

@app.route('/admin/accounts')
def admin_accounts():
    """Account management page for admins - optimized with single query"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    # Optimized query using subqueries and aggregation
    from sqlalchemy import func
    
    # Create subqueries for counts
    post_counts = db.session.query(
        Post.user_id,
        func.count(Post.id).label('post_count')
    ).group_by(Post.user_id).subquery()
    
    message_counts = db.session.query(
        DirectMessage.sender_id,
        func.count(DirectMessage.id).label('message_count')
    ).group_by(DirectMessage.sender_id).subquery()
    
    group_counts = db.session.query(
        GroupMembership.user_id,
        func.count(GroupMembership.id).label('group_count')
    ).group_by(GroupMembership.user_id).subquery()
    
    # Single optimized query with left joins
    user_stats = db.session.query(
        User,
        func.coalesce(post_counts.c.post_count, 0).label('post_count'),
        func.coalesce(message_counts.c.message_count, 0).label('message_count'),
        func.coalesce(group_counts.c.group_count, 0).label('group_count')
    ).outerjoin(post_counts, User.id == post_counts.c.user_id)\
     .outerjoin(message_counts, User.id == message_counts.c.sender_id)\
     .outerjoin(group_counts, User.id == group_counts.c.user_id)\
     .order_by(User.date_created.desc()).all()
    
    return render_template('admin_accounts.html', user_stats=user_stats)

@app.route('/admin/edit_user/<int:user_id>', methods=['GET', 'POST'])
def admin_edit_user(user_id):
    """Edit user account (admin only)"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        user.username = request.form['username'].strip()
        user.email = request.form['email'].strip()
        user.full_name = request.form['full_name'].strip()
        user.bio = request.form.get('bio', '').strip()
        
        # Handle teacher-specific fields
        if user.is_teacher:
            user.subject_taught = request.form.get('subject_taught', '').strip()
            user.class_teacher_grade = request.form.get('class_teacher_grade', '') or None
            user.class_teacher_section = request.form.get('class_teacher_section', '') or None
        else:
            # Handle student-specific fields
            user.grade_level = request.form.get('grade_level', '')
            user.section = request.form.get('section', '')
            user.house = request.form.get('house') or None
            user.is_prefect = 'is_prefect' in request.form
        
        # Handle password change if provided
        new_password = request.form.get('password', '').strip()
        if new_password:
            user.set_password(new_password)
        
        db.session.commit()
        flash(f'User {user.full_name} updated successfully.', 'success')
        return redirect(url_for('admin_accounts'))
    
    return render_template('admin_edit_user.html', user=user)

@app.route('/admin/delete_user/<int:user_id>', methods=['POST'])
def admin_delete_user(user_id):
    """Delete user account (admin only) with proper cleanup"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    
    # Don't allow deleting the current admin user
    if user.id == session['user_id']:
        flash('Cannot delete your own admin account.', 'error')
        return redirect(url_for('admin_accounts'))
    
    # Don't allow deleting other admin users
    if user.is_admin:
        flash('Cannot delete other admin accounts.', 'error')
        return redirect(url_for('admin_accounts'))
    
    username = user.username
    full_name = user.full_name
    
    try:
        # Manual cleanup of related records to avoid constraint issues
        # Delete parent-child relationships
        ParentChild.query.filter(
            (ParentChild.parent_id == user.id) | (ParentChild.child_id == user.id)
        ).delete()
        
        # Delete group memberships
        GroupMembership.query.filter_by(user_id=user.id).delete()
        
        # Delete group messages
        GroupMessage.query.filter_by(user_id=user.id).delete()
        
        # Delete direct messages
        DirectMessage.query.filter(
            (DirectMessage.sender_id == user.id) | (DirectMessage.receiver_id == user.id)
        ).delete()
        
        # Delete likes
        Like.query.filter_by(user_id=user.id).delete()
        
        # Delete comments
        Comment.query.filter_by(user_id=user.id).delete()
        
        # Delete posts
        Post.query.filter_by(user_id=user.id).delete()
        
        # Delete groups where user is admin
        Group.query.filter_by(admin_id=user.id).delete()
        
        # Delete user account
        db.session.delete(user)
        db.session.commit()
        
        flash(f'User account {full_name} ({username}) has been permanently deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        logging.error(f"Error deleting user {user_id}: {str(e)}")
        flash(f'Error deleting user account. Please try again.', 'error')
    
    return redirect(url_for('admin_accounts'))

@app.route('/parent/dashboard')
def parent_dashboard():
    """Parent dashboard page - view children's activities and progress"""
    if 'user_id' not in session or not session.get('is_parent'):
        flash('Access denied. Parent privileges required.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    children = user.get_children()
    
    # Get all recent posts for social feed view (not just children's)
    children_posts = Post.query\
        .options(joinedload(Post.author))\
        .order_by(Post.date_posted.desc())\
        .limit(20).all()
    
    # Get recent messages from children
    children_messages = []
    if children:
        children_ids = [child.id for child in children]
        children_messages = DirectMessage.query.filter(
            DirectMessage.sender_id.in_(children_ids)
        ).options(joinedload(DirectMessage.sender), joinedload(DirectMessage.receiver))\
        .order_by(DirectMessage.date_sent.desc()).limit(10).all()
    
    # Get homework assignments for children
    children_homework = []
    if children:
        children_homework = Homework.query.order_by(Homework.due_date.desc()).limit(10).all()
    
    # If no children assigned, create a test relationship with student1 for demo
    if not children:
        from models import ParentChild
        student = User.query.filter_by(username='student1').first()
        if student and not ParentChild.query.filter_by(parent_id=user.id, child_id=student.id).first():
            pc = ParentChild(parent_id=user.id, child_id=student.id)
            db.session.add(pc)
            db.session.commit()
            children = user.get_children()  # Refresh children list
    
    return render_template('parent_dashboard.html',
                         user=user,
                         children=children,
                         children_posts=children_posts,
                         children_messages=children_messages,
                         children_homework=children_homework)

@app.route('/api/child_activity_data/<int:child_id>')
def child_activity_data(child_id):
    """API endpoint to get real child posting activity data"""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    current_user = User.query.get(session['user_id'])
    
    # Only allow parents to access this data
    if not current_user.is_parent:
        return jsonify({'error': 'Access denied'}), 403
    
    # Verify this child belongs to the parent
    child_relationship = ParentChild.query.filter_by(
        parent_id=current_user.id,
        child_id=child_id
    ).first()
    
    if not child_relationship:
        return jsonify({'error': 'Child not found'}), 404
    
    # Get real activity data
    from models import DailyPostingActivity
    activities = DailyPostingActivity.get_user_activity_last_30_days(child_id)
    
    # Format for Chart.js
    labels = [activity['date'].strftime('%b %d') for activity in activities]
    data = [activity['count'] for activity in activities]
    
    return jsonify({
        'labels': labels,
        'data': data,
        'child_id': child_id
    })

@app.route('/student/dashboard')
def student_dashboard():
    """Student dashboard with posts feed - optimized with pagination and eager loading"""
    if 'user_id' not in session:
        flash('Please log in to access the dashboard.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Check if user is limited and block access to social feed
    if current_user.is_limited_currently():
        limit_message = f'Your access to social features is limited. Reason: {current_user.limit_reason}'
        if current_user.limit_end_date:
            limit_message += f' Limitation expires on {current_user.limit_end_date.strftime("%B %d, %Y at %I:%M %p")}'
        else:
            limit_message += ' This is a permanent limitation.'
        flash(limit_message, 'warning')
        return redirect(url_for('profile'))  # Redirect to profile instead
    
    # Pagination for posts
    page = request.args.get('page', 1, type=int)
    per_page = 20  # Show 20 posts per page
    
    # Optimized query with eager loading and pagination
    posts_pagination = Post.query\
        .options(joinedload(Post.author))\
        .order_by(Post.date_posted.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    current_user = User.query.get(session['user_id'])
    
    # Optimized unread count using property
    unread_count = current_user.unread_message_count
    
    return render_template('student_dashboard.html', 
                         posts=posts_pagination.items,
                         pagination=posts_pagination,
                         current_user=current_user, 
                         unread_messages=unread_count)

@app.route('/profile')
def profile():
    """User profile page - optimized with pagination"""
    if 'user_id' not in session:
        flash('Please log in to view your profile.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    # Pagination for user posts
    page = request.args.get('page', 1, type=int)
    per_page = 15  # Show 15 posts per page
    
    user_posts_pagination = Post.query\
        .filter_by(user_id=user.id)\
        .order_by(Post.date_posted.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    # Use optimized unread count
    unread_count = user.unread_message_count if not session.get('is_admin') else 0
    
    return render_template('profile.html', 
                         user=user, 
                         posts=user_posts_pagination.items,
                         pagination=user_posts_pagination,
                         unread_messages=unread_count)

@app.route('/edit_profile', methods=['GET', 'POST'])
def edit_profile():
    """Edit user profile"""
    if 'user_id' not in session:
        flash('Please log in to edit your profile.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    if request.method == 'POST':
        new_username = request.form['username'].strip()
        new_bio = request.form['bio'].strip()
        
        # Check if username is taken by another user
        if new_username != user.username:
            existing_user = User.query.filter_by(username=new_username).first()
            if existing_user:
                flash('Username already taken. Please choose a different one.', 'error')
                return render_template('edit_profile.html', user=user)
        
        # Update user information
        user.username = new_username
        user.bio = new_bio
        
        db.session.commit()
        
        # Update session username
        session['username'] = new_username
        
        flash('Profile updated successfully!', 'success')
        return redirect(url_for('profile'))
    
    return render_template('edit_profile.html', user=user)

@app.route('/user/<username>')
def view_user_profile(username):
    """View another user's public profile - optimized with pagination"""
    if 'user_id' not in session:
        flash('Please log in to view profiles.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.filter_by(username=username).first_or_404()
    current_user = User.query.get(session['user_id'])
    
    # Pagination for user posts
    page = request.args.get('page', 1, type=int)
    per_page = 15  # Show 15 posts per page
    
    user_posts_pagination = Post.query\
        .filter_by(user_id=user.id)\
        .order_by(Post.date_posted.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('view_profile.html', 
                         user=user, 
                         posts=user_posts_pagination.items,
                         pagination=user_posts_pagination,
                         current_user=current_user)

@app.route('/create_post', methods=['GET', 'POST'])
def create_post():
    """Create new post - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to create posts.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Disable post creation for parents
    if current_user.is_parent:
        flash('Parents cannot create posts. This is a monitoring-only account.', 'warning')
        return redirect(url_for('parent_dashboard'))
    
    # Check if user is limited and block access to create posts
    if current_user.is_limited_currently():
        flash('You cannot create posts while your account is limited.', 'warning')
        return redirect(url_for('profile'))
    
    if request.method == 'POST':
        content = request.form['content'].strip()
        
        if not content:
            flash('Post content cannot be empty.', 'error')
            unread_count = DirectMessage.query.filter_by(receiver_id=session['user_id'], is_read=False).count()
            return render_template('create_post.html', unread_messages=unread_count)
        
        new_post = Post(
            content=content,
            user_id=session['user_id']
        )
        
        db.session.add(new_post)
        db.session.commit()
        
        # Record daily posting activity
        from models import DailyPostingActivity
        DailyPostingActivity.record_post(session['user_id'])
        
        flash('Post created successfully!', 'success')
        return redirect(url_for('student_dashboard'))
    
    # Use optimized unread count
    current_user = User.query.get(session['user_id'])
    unread_count = current_user.unread_message_count
    return render_template('create_post.html', unread_messages=unread_count)

@app.route('/like_post/<int:post_id>')
def like_post(post_id):
    """Toggle like on a post - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to like posts.', 'error')
        return redirect(url_for('login'))
    
    post = Post.query.get_or_404(post_id)
    user_id = session['user_id']
    
    # Check if user already liked this post
    existing_like = Like.query.filter_by(user_id=user_id, post_id=post_id).first()
    
    if existing_like:
        # Unlike the post
        db.session.delete(existing_like)
        flash('Post unliked.', 'info')
    else:
        # Like the post
        new_like = Like(user_id=user_id, post_id=post_id)
        db.session.add(new_like)
        flash('Post liked!', 'success')
    
    db.session.commit()
    return redirect(url_for('student_dashboard'))

@app.route('/comment_post/<int:post_id>', methods=['POST'])
def comment_post(post_id):
    """Add comment to a post - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to comment on posts.', 'error')
        return redirect(url_for('login'))
    
    post = Post.query.get_or_404(post_id)
    content = request.form['content'].strip()
    
    if not content:
        flash('Comment cannot be empty.', 'error')
        return redirect(url_for('student_dashboard'))
    
    new_comment = Comment(
        content=content,
        user_id=session['user_id'],
        post_id=post_id
    )
    
    db.session.add(new_comment)
    db.session.commit()
    
    flash('Comment added successfully!', 'success')
    return redirect(url_for('student_dashboard'))

@app.route('/delete_post/<int:post_id>')
def delete_post(post_id):
    """Delete a post - only prefects and admins can delete posts"""
    if 'user_id' not in session:
        flash('Please log in to delete posts.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    post = Post.query.get_or_404(post_id)
    
    # Only admin, teachers, prefects, or post author can delete
    if not current_user.can_delete_post(post):
        flash('Access denied. Only teachers, prefects, admins, or post authors can delete posts.', 'error')
        return redirect(url_for('student_dashboard'))
    
    # Delete associated comments and likes first
    Comment.query.filter_by(post_id=post_id).delete()
    Like.query.filter_by(post_id=post_id).delete()
    
    db.session.delete(post)
    db.session.commit()
    
    flash('Post deleted successfully.', 'success')
    return redirect(url_for('student_dashboard'))

@app.route('/delete_comment/<int:comment_id>')
def delete_comment(comment_id):
    """Delete a comment - only prefects and admins can delete comments"""
    if 'user_id' not in session:
        flash('Please log in to delete comments.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    comment = Comment.query.get_or_404(comment_id)
    
    # Only admin, teachers, prefects, or comment author can delete
    if not (current_user.is_admin or current_user.is_teacher or current_user.is_prefect or comment.user_id == current_user.id):
        flash('Access denied. Only teachers, prefects, admins, or comment authors can delete comments.', 'error')
        return redirect(url_for('student_dashboard'))
    
    db.session.delete(comment)
    db.session.commit()
    
    flash('Comment deleted successfully.', 'success')
    return redirect(url_for('student_dashboard'))

@app.route('/admin/communications_panel')
def admin_communications_panel():
    """Admin panel to monitor all DMs and group messages"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    # Get all direct messages
    direct_messages = DirectMessage.query.order_by(DirectMessage.date_sent.desc()).limit(100).all()
    
    # Get all group messages  
    group_messages = GroupMessage.query.order_by(GroupMessage.date_sent.desc()).limit(100).all()
    
    # Get all groups
    all_groups = Group.query.order_by(Group.name).all()
    
    return render_template('admin_communications_panel.html', 
                         direct_messages=direct_messages,
                         group_messages=group_messages,
                         all_groups=all_groups)



@app.route('/messages')
def messages():
    """View all users for direct messaging - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to access messages.', 'error')
        return redirect(url_for('login'))
    
    current_user_id = session['user_id']
    current_user = User.query.get(current_user_id)
    
    # Disable messaging for parents
    if current_user.is_parent:
        flash('Parents cannot send or receive messages. This is a monitoring-only account.', 'warning')
        return redirect(url_for('parent_dashboard'))
    
    # Check if user is limited and block access to messages
    if current_user.is_limited_currently():
        flash('You cannot access messages while your account is limited.', 'warning')
        return redirect(url_for('profile'))
    
    # Admin can message everyone, students can message other students and admin
    if current_user.is_admin:
        all_students = User.query.filter(User.id != current_user_id).filter(User.is_parent == False).order_by(User.full_name).all()
    else:
        # Students can message other students and the admin (but not parents)
        all_students = User.query.filter(User.id != current_user_id).filter(User.is_parent == False).order_by(User.full_name).all()
    
    # Get conversation data for each student
    students_data = []
    for student in all_students:
        # Get last message between current user and this student
        last_message = DirectMessage.query.filter(
            or_(
                and_(DirectMessage.sender_id == current_user_id, DirectMessage.receiver_id == student.id),
                and_(DirectMessage.sender_id == student.id, DirectMessage.receiver_id == current_user_id)
            )
        ).order_by(DirectMessage.date_sent.desc()).first()
        
        # Count unread messages from this student
        unread_count = DirectMessage.query.filter_by(
            sender_id=student.id, 
            receiver_id=current_user_id, 
            is_read=False
        ).count()
        
        students_data.append({
            'user': student,
            'last_message': last_message,
            'unread_count': unread_count,
            'has_conversation': last_message is not None
        })
    
    # Sort by: 1) Has conversation, 2) Last message date, 3) Name
    students_data.sort(key=lambda x: (
        not x['has_conversation'],  # Conversations first
        -(x['last_message'].date_sent.timestamp() if x['last_message'] else 0),  # Recent messages first
        x['user'].full_name.lower()  # Then alphabetical
    ))
    
    # Get unread message count for navigation
    unread_count = DirectMessage.query.filter_by(receiver_id=current_user_id, is_read=False).count()
    
    return render_template('messages.html', students=students_data, unread_messages=unread_count)

@app.route('/message/<username>')
def view_conversation(username):
    """View conversation with specific user - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to view conversations.', 'error')
        return redirect(url_for('login'))
    
    current_user_id = session['user_id']
    other_user = User.query.filter_by(username=username).first_or_404()
    current_user = User.query.get(current_user_id)
    
    # Disable messaging for parents
    if current_user.is_parent:
        flash('Parents cannot access messaging conversations.', 'warning')
        return redirect(url_for('parent_dashboard'))
    
    # Get all messages between these two users
    messages = DirectMessage.query.filter(
        or_(
            and_(DirectMessage.sender_id == current_user_id, DirectMessage.receiver_id == other_user.id),
            and_(DirectMessage.sender_id == other_user.id, DirectMessage.receiver_id == current_user_id)
        )
    ).order_by(DirectMessage.date_sent.asc()).all()
    
    # Mark messages from other user as read
    unread_messages = DirectMessage.query.filter_by(
        sender_id=other_user.id,
        receiver_id=current_user_id,
        is_read=False
    ).all()
    
    for msg in unread_messages:
        msg.is_read = True
    db.session.commit()
    
    return render_template('conversation.html', other_user=other_user, messages=messages, current_user=current_user)

@app.route('/send_message/<username>', methods=['POST'])
def send_message(username):
    """Send a direct message - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to send messages.', 'error')
        return redirect(url_for('login'))
    
    current_user_id = session['user_id']
    current_user = User.query.get(current_user_id)
    receiver = User.query.filter_by(username=username).first_or_404()
    content = request.form['content'].strip()
    
    # Disable messaging for parents
    if current_user.is_parent or receiver.is_parent:
        flash('Messaging to/from parent accounts is not allowed.', 'warning')
        return redirect(url_for('parent_dashboard') if current_user.is_parent else url_for('messages'))
    
    if not content:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('view_conversation', username=username))
    

    
    # Create new message
    new_message = DirectMessage(
        sender_id=current_user_id,
        receiver_id=receiver.id,
        content=content
    )
    
    db.session.add(new_message)
    db.session.commit()
    
    # Redirect back to conversation with a fragment to maintain scroll position
    return redirect(url_for('view_conversation', username=username) + '#bottom')

@app.route('/edit_message/<int:message_id>', methods=['POST'])
def edit_direct_message(message_id):
    """Edit a direct message"""
    if 'user_id' not in session:
        flash('Please log in to edit messages.', 'error')
        return redirect(url_for('login'))
    
    message = DirectMessage.query.get_or_404(message_id)
    
    # Only the sender can edit their message
    if message.sender_id != session['user_id']:
        flash('You can only edit your own messages.', 'error')
        return redirect(request.referrer or url_for('messages'))
    
    new_content = request.form.get('content', '').strip()
    if not new_content:
        flash('Message content cannot be empty.', 'error')
        return redirect(request.referrer or url_for('messages'))
    
    message.content = new_content
    message.is_edited = True
    message.date_edited = get_ist_now()
    db.session.commit()
    
    flash('Message edited successfully.', 'success')
    return redirect(request.referrer or url_for('messages'))

@app.route('/delete_message/<int:message_id>', methods=['POST'])
def delete_direct_message(message_id):
    """Delete a direct message"""
    if 'user_id' not in session:
        flash('Please log in to delete messages.', 'error')
        return redirect(url_for('login'))
    
    message = DirectMessage.query.get_or_404(message_id)
    
    # Only the sender can delete their message
    if message.sender_id != session['user_id']:
        flash('You can only delete your own messages.', 'error')
        return redirect(request.referrer or url_for('messages'))
    
    db.session.delete(message)
    db.session.commit()
    
    flash('Message deleted successfully.', 'success')
    return redirect(request.referrer or url_for('messages'))

@app.route('/new_message', methods=['GET', 'POST'])
def new_message():
    """Start a new conversation - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to start conversations.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        username = request.form['username'].strip()
        content = request.form['content'].strip()
        
        if not username or not content:
            flash('Please fill in all fields.', 'error')
            return render_template('new_message.html')
        
        receiver = User.query.filter_by(username=username).first()
        
        if not receiver:
            flash('User not found.', 'error')
            return render_template('new_message.html')
        

        
        if receiver.id == session['user_id']:
            flash('Cannot message yourself.', 'error')
            return render_template('new_message.html')
        
        # Create new message
        new_message = DirectMessage(
            sender_id=session['user_id'],
            receiver_id=receiver.id,
            content=content
        )
        
        db.session.add(new_message)
        db.session.commit()
        
        return redirect(url_for('view_conversation', username=username))
    
    # Get all students for autocomplete
    students = User.query.filter_by(is_admin=False).filter(User.id != session['user_id']).all()
    return render_template('new_message.html', students=students)

# =================== GROUP ROUTES ===================

@app.route('/groups')
def groups():
    """View all groups - optimized with pagination and eager loading"""
    if 'user_id' not in session:
        flash('Please log in to view groups.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Disable groups for parents
    if current_user.is_parent:
        flash('Parents cannot access groups. This is a monitoring-only account.', 'warning')
        return redirect(url_for('parent_dashboard'))
    
    # Check if user is limited and block access to groups
    if current_user.is_limited_currently():
        flash('You cannot access groups while your account is limited.', 'warning')
        return redirect(url_for('profile'))
    
    # Pagination for public groups
    page = request.args.get('page', 1, type=int)
    per_page = 15  # Show 15 groups per page
    
    # Get groups with pagination and eager loading - admins can see all groups
    if current_user.is_admin:
        # Admins can see all groups (both public and private)
        groups_query = Group.query
    else:
        # Regular users only see public groups
        groups_query = Group.query.filter_by(is_private=False)
    
    public_groups_pagination = groups_query\
        .options(joinedload(Group.admin))\
        .order_by(Group.date_created.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    # Get user's groups (optimized)
    user_groups = current_user.get_groups()
    
    # Use optimized unread count
    unread_count = current_user.unread_message_count
    
    return render_template('groups.html', 
                         public_groups=public_groups_pagination.items,
                         pagination=public_groups_pagination,
                         user_groups=user_groups, 
                         current_user=current_user,
                         unread_messages=unread_count)

@app.route('/create_group', methods=['GET', 'POST'])
def create_group():
    """Create a new group - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to create groups.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        is_private = 'is_private' in request.form
        
        if not name:
            flash('Group name is required.', 'error')
            return render_template('create_group.html')
        
        # Check if group name already exists
        existing_group = Group.query.filter_by(name=name).first()
        if existing_group:
            flash('A group with this name already exists.', 'error')
            return render_template('create_group.html')
        
        # Create new group
        invite_code = Group.generate_invite_code() if is_private else None
        new_group = Group(
            name=name,
            description=description,
            is_private=is_private,
            invite_code=invite_code,
            admin_id=session['user_id']
        )
        
        db.session.add(new_group)
        db.session.flush()  # Get the group ID
        
        # Add creator as first member
        membership = GroupMembership(
            user_id=session['user_id'],
            group_id=new_group.id
        )
        db.session.add(membership)
        db.session.commit()
        
        flash(f'Group "{name}" created successfully!', 'success')
        return redirect(url_for('view_group', group_id=new_group.id))
    
    # Use optimized unread count
    current_user = User.query.get(session['user_id'])
    unread_count = current_user.unread_message_count
    return render_template('create_group.html', unread_messages=unread_count)

@app.route('/group/<int:group_id>')
def view_group(group_id):
    """View a specific group - optimized with eager loading and pagination"""
    if 'user_id' not in session:
        flash('Please log in to view groups.', 'error')
        return redirect(url_for('login'))
    
    # Get group with eager loading of admin
    group = Group.query.options(joinedload(Group.admin)).get_or_404(group_id)
    current_user = User.query.get(session['user_id'])
    
    # Check if user can access this group - admins can access all groups
    if group.is_private and not group.is_member(current_user) and not current_user.is_admin:
        flash('This is a private group. You need an invite code to join.', 'error')
        return redirect(url_for('groups'))
    
    # Get group members with eager loading
    members_query = GroupMembership.query\
        .filter_by(group_id=group.id)\
        .options(joinedload(GroupMembership.user))\
        .all()
    members = [membership.user for membership in members_query]
    
    # Pagination for group messages
    page = request.args.get('page', 1, type=int)
    per_page = 30  # Show 30 messages per page
    
    # Get group messages with pagination and eager loading
    messages_pagination = GroupMessage.query\
        .filter_by(group_id=group.id)\
        .options(joinedload(GroupMessage.user))\
        .order_by(GroupMessage.date_sent.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
    
    # Reverse to show oldest first
    group_messages = list(reversed(messages_pagination.items))
    
    # Use optimized unread count
    unread_count = current_user.unread_message_count
    
    return render_template('view_group.html', 
                         group=group, 
                         members=members, 
                         group_messages=group_messages,
                         pagination=messages_pagination,
                         current_user=current_user,
                         is_admin=(group.admin_id == current_user.id),
                         unread_messages=unread_count)

@app.route('/join_group', methods=['POST'])
def join_group():
    """Join a group using invite code or direct join for public groups - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to join groups.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Check if it's an invite code join or public group join
    if 'invite_code' in request.form:
        invite_code = request.form['invite_code'].strip().upper()
        
        if not invite_code:
            flash('Please enter an invite code.', 'error')
            return redirect(url_for('groups'))
        
        group = Group.query.filter_by(invite_code=invite_code).first()
        if not group:
            flash('Invalid invite code.', 'error')
            return redirect(url_for('groups'))
            
    elif 'group_id' in request.form:
        group_id = request.form['group_id']
        group = Group.query.get_or_404(group_id)
        
        # Can only join public groups directly
        if group.is_private:
            flash('This is a private group. You need an invite code to join.', 'error')
            return redirect(url_for('groups'))
    else:
        flash('Invalid join request.', 'error')
        return redirect(url_for('groups'))
    
    # Check if user is already a member
    if group.is_member(current_user):
        flash('You are already a member of this group.', 'success')
        return redirect(url_for('view_group', group_id=group.id))
    
    # Add user to group
    membership = GroupMembership(
        user_id=current_user.id,
        group_id=group.id
    )
    db.session.add(membership)
    db.session.commit()
    
    flash(f'Successfully joined "{group.name}"!', 'success')
    return redirect(url_for('view_group', group_id=group.id))

@app.route('/leave_group/<int:group_id>')
def leave_group(group_id):
    """Leave a group - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to leave groups.', 'error')
        return redirect(url_for('login'))
    
    group = Group.query.get_or_404(group_id)
    current_user = User.query.get(session['user_id'])
    
    # Check if user is admin
    if group.admin_id == current_user.id:
        flash('Group admins cannot leave their own groups. Delete the group instead.', 'error')
        return redirect(url_for('view_group', group_id=group_id))
    
    # Remove membership
    membership = GroupMembership.query.filter_by(user_id=current_user.id, group_id=group.id).first()
    if membership:
        db.session.delete(membership)
        db.session.commit()
        flash(f'You left "{group.name}".', 'info')
    
    return redirect(url_for('groups'))

@app.route('/delete_group/<int:group_id>')
def delete_group(group_id):
    """Delete a group - group admins and site admins can delete groups"""
    if 'user_id' not in session:
        flash('Please log in to delete groups.', 'error')
        return redirect(url_for('login'))
    
    group = Group.query.get_or_404(group_id)
    current_user = User.query.get(session['user_id'])
    
    # Admins can delete any group, group admins can delete their own groups
    if not (current_user.is_admin or group.admin_id == current_user.id):
        flash('Access denied. Only group admins or site admins can delete groups.', 'error')
        return redirect(url_for('view_group', group_id=group_id))
    
    # Delete all group messages and memberships first
    GroupMessage.query.filter_by(group_id=group_id).delete()
    GroupMembership.query.filter_by(group_id=group_id).delete()
    
    # Delete the group
    db.session.delete(group)
    db.session.commit()
    
    flash(f'Group "{group.name}" deleted successfully.', 'success')
    return redirect(url_for('groups'))

@app.route('/send_group_message/<int:group_id>', methods=['POST'])
def send_group_message(group_id):
    """Send a message to a group - accessible by both students and admin"""
    if 'user_id' not in session:
        flash('Please log in to send messages.', 'error')
        return redirect(url_for('login'))
    
    group = Group.query.get_or_404(group_id)
    current_user = User.query.get(session['user_id'])
    
    # Check if user is a member of this group
    if not group.is_member(current_user):
        flash('You must be a member to send messages in this group.', 'error')
        return redirect(url_for('groups'))
    
    content = request.form['content'].strip()
    
    if not content:
        flash('Message cannot be empty.', 'error')
        return redirect(url_for('view_group', group_id=group_id))
    
    # Create new group message
    new_message = GroupMessage(
        group_id=group_id,
        user_id=current_user.id,
        content=content
    )
    
    db.session.add(new_message)
    db.session.commit()
    
    return redirect(url_for('view_group', group_id=group_id) + '#bottom')

# AJAX endpoints for real-time messaging
@app.route('/api/messages/<username>')
def get_messages_json(username):
    """Get direct messages as JSON for AJAX polling"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    current_user_id = session['user_id']
    other_user = User.query.filter_by(username=username).first_or_404()
    
    # Get messages with a timestamp filter to only get recent messages
    since_timestamp = request.args.get('since', '0')
    since_datetime = datetime.fromtimestamp(float(since_timestamp)) if since_timestamp != '0' else datetime.min
    
    messages = DirectMessage.query.filter(
        or_(
            and_(DirectMessage.sender_id == current_user_id, DirectMessage.receiver_id == other_user.id),
            and_(DirectMessage.sender_id == other_user.id, DirectMessage.receiver_id == current_user_id)
        ),
        DirectMessage.date_sent > since_datetime
    ).order_by(DirectMessage.date_sent.asc()).all()
    
    # Mark new messages from other user as read
    unread_messages = DirectMessage.query.filter_by(
        sender_id=other_user.id,
        receiver_id=current_user_id,
        is_read=False
    ).all()
    
    for msg in unread_messages:
        msg.is_read = True
    db.session.commit()
    
    messages_data = []
    for msg in messages:
        messages_data.append({
            'id': msg.id,
            'content': msg.content,
            'sender_id': msg.sender_id,
            'sender_name': msg.sender.full_name,
            'date_sent': msg.date_sent.isoformat(),
            'timestamp': msg.date_sent.timestamp(),
            'is_edited': msg.is_edited,
            'formatted_time': msg.date_sent.strftime('%I:%M %p')
        })
    
    return jsonify({'messages': messages_data, 'current_user_id': current_user_id})

@app.route('/api/notifications/check')
def check_notifications():
    """Check for new messages and return notification count for global notifications"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    current_user_id = session['user_id']
    current_user = User.query.get(current_user_id)
    
    # Get total unread message count
    unread_count = current_user.unread_message_count
    
    # Get latest unread messages with sender info for notifications
    latest_unread = DirectMessage.query.filter_by(
        receiver_id=current_user_id,
        is_read=False
    ).order_by(DirectMessage.date_sent.desc()).limit(5).all()
    
    notifications = []
    for msg in latest_unread:
        notifications.append({
            'id': msg.id,
            'sender_name': msg.sender.full_name,
            'sender_username': msg.sender.username,
            'content_preview': msg.content[:50] + '...' if len(msg.content) > 50 else msg.content,
            'timestamp': msg.date_sent.timestamp(),
            'formatted_time': msg.date_sent.strftime('%I:%M %p')
        })
    
    return jsonify({
        'unread_count': unread_count,
        'notifications': notifications,
        'has_new_messages': unread_count > 0
    })

@app.route('/api/conversations/list')
def get_conversations_list():
    """Get updated conversation list for real-time updates on messages page"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    current_user_id = session['user_id']
    current_user = User.query.get(current_user_id)
    
    # Disable messaging for parents
    if current_user.is_parent:
        return jsonify({'error': 'Access denied'}), 403
    
    # Get all available users for messaging
    if current_user.is_admin:
        all_users = User.query.filter(User.id != current_user_id).filter(User.is_parent == False).order_by(User.full_name).all()
    else:
        all_users = User.query.filter(User.id != current_user_id).filter(User.is_parent == False).order_by(User.full_name).all()
    
    # Get conversation data for each user
    conversations_data = []
    for user in all_users:
        # Get last message between current user and this user
        last_message = DirectMessage.query.filter(
            or_(
                and_(DirectMessage.sender_id == current_user_id, DirectMessage.receiver_id == user.id),
                and_(DirectMessage.sender_id == user.id, DirectMessage.receiver_id == current_user_id)
            )
        ).order_by(DirectMessage.date_sent.desc()).first()
        
        # Count unread messages from this user
        unread_count = DirectMessage.query.filter_by(
            sender_id=user.id, 
            receiver_id=current_user_id, 
            is_read=False
        ).count()
        
        conversation_data = {
            'user_id': user.id,
            'username': user.username,
            'full_name': user.full_name,
            'initial': user.full_name[0].upper(),
            'is_admin': user.is_admin,
            'is_teacher': user.is_teacher,
            'is_prefect': user.is_prefect,
            'house': user.house,
            'grade_level': user.grade_level,
            'unread_count': unread_count,
            'has_conversation': last_message is not None
        }
        
        if last_message:
            conversation_data['last_message'] = {
                'content': last_message.content,
                'sender_id': last_message.sender_id,
                'sender_name': last_message.sender.full_name,
                'timestamp': last_message.date_sent.timestamp(),
                'formatted_time': last_message.date_sent.strftime('%b %d, %Y at %I:%M %p')
            }
        
        conversations_data.append(conversation_data)
    
    # Sort by: 1) Has conversation, 2) Last message date, 3) Name
    conversations_data.sort(key=lambda x: (
        not x['has_conversation'],  # Conversations first
        -(x['last_message']['timestamp'] if x.get('last_message') else 0),  # Recent messages first
        x['full_name'].lower()  # Then alphabetical
    ))
    
    return jsonify({
        'conversations': conversations_data,
        'current_user_id': current_user_id
    })

@app.route('/api/group/<int:group_id>/messages')
def get_group_messages_json(group_id):
    """Get group messages as JSON for AJAX polling"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    group = Group.query.get_or_404(group_id)
    current_user = User.query.get(session['user_id'])
    
    # Check if user can access this group
    if group.is_private and not group.is_member(current_user) and not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    # Get messages with a timestamp filter to only get recent messages
    since_timestamp = request.args.get('since', '0')
    since_datetime = datetime.fromtimestamp(float(since_timestamp)) if since_timestamp != '0' else datetime.min
    
    messages = GroupMessage.query.filter(
        GroupMessage.group_id == group_id,
        GroupMessage.date_sent > since_datetime
    ).order_by(GroupMessage.date_sent.asc()).all()
    
    messages_data = []
    for msg in messages:
        user_badges = []
        if msg.user.is_admin:
            user_badges.append({'icon': 'fas fa-crown', 'color': 'text-danger', 'title': 'Admin'})
        elif msg.user.is_teacher:
            user_badges.append({'icon': 'fas fa-chalkboard-teacher', 'color': 'text-success', 'title': 'Teacher'})
        elif msg.user.is_prefect:
            user_badges.append({'icon': 'fas fa-star', 'color': 'text-warning', 'title': 'Prefect'})
        
        if msg.user.id == group.admin_id:
            user_badges.append({'icon': 'fas fa-crown', 'color': 'text-warning', 'title': 'Group Admin'})
        
        messages_data.append({
            'id': msg.id,
            'content': msg.content,
            'user_id': msg.user_id,
            'user_name': msg.user.full_name,
            'user_username': msg.user.username,
            'user_initial': msg.user.full_name[0].upper(),
            'user_badges': user_badges,
            'date_sent': msg.date_sent.isoformat(),
            'timestamp': msg.date_sent.timestamp(),
            'is_edited': msg.is_edited,
            'formatted_time': msg.date_sent.strftime('%I:%M %p'),
            'can_edit': msg.user_id == current_user.id,
            'can_report': (current_user.is_prefect or current_user.is_teacher or current_user.is_admin) and msg.user_id != current_user.id and current_user.can_report_or_delete_user(msg.user)
        })
    
    return jsonify({'messages': messages_data, 'current_user_id': current_user.id})

@app.route('/api/send_message/<username>', methods=['POST'])
def send_message_ajax(username):
    """Send a direct message via AJAX"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    current_user_id = session['user_id']
    current_user = User.query.get(current_user_id)
    receiver = User.query.filter_by(username=username).first_or_404()
    
    # Check permissions
    if current_user.is_parent or receiver.is_parent:
        return jsonify({'error': 'Messaging to/from parent accounts is not allowed'}), 403
    
    # Handle both JSON and form data for browser compatibility
    try:
        content = ''
        if request.is_json:
            data = request.get_json()
            if data:
                content = data.get('content', '').strip()
        elif request.form:
            content = request.form.get('content', '').strip()
        else:
            # Last fallback - try to get raw data
            raw_data = request.get_data(as_text=True)
            if raw_data:
                try:
                    import json
                    data = json.loads(raw_data)
                    content = data.get('content', '').strip()
                except:
                    pass
    except Exception as e:
        app.logger.error(f'Error parsing direct message request: {e}')
        return jsonify({'error': 'Invalid request format'}), 400
    
    if not content:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    # Create new message
    new_message = DirectMessage(
        sender_id=current_user_id,
        receiver_id=receiver.id,
        content=content
    )
    
    db.session.add(new_message)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': {
            'id': new_message.id,
            'content': new_message.content,
            'sender_id': new_message.sender_id,
            'sender_name': new_message.sender.full_name,
            'date_sent': new_message.date_sent.isoformat(),
            'timestamp': new_message.date_sent.timestamp(),
            'formatted_time': new_message.date_sent.strftime('%I:%M %p'),
            'is_edited': False
        }
    })

@app.route('/api/send_group_message/<int:group_id>', methods=['POST'])
def send_group_message_ajax(group_id):
    """Send a group message via AJAX"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    group = Group.query.get_or_404(group_id)
    current_user = User.query.get(session['user_id'])
    
    # Check if user is a member of this group
    if not group.is_member(current_user):
        return jsonify({'error': 'You must be a member to send messages in this group'}), 403
    
    # Handle both JSON and form data for browser compatibility
    try:
        content = ''
        if request.is_json:
            data = request.get_json()
            if data:
                content = data.get('content', '').strip()
        elif request.form:
            content = request.form.get('content', '').strip()
        else:
            # Last fallback - try to get raw data
            raw_data = request.get_data(as_text=True)
            if raw_data:
                try:
                    import json
                    data = json.loads(raw_data)
                    content = data.get('content', '').strip()
                except:
                    pass
    except Exception as e:
        app.logger.error(f'Error parsing group message request: {e}')
        return jsonify({'error': 'Invalid request format'}), 400
    
    if not content:
        return jsonify({'error': 'Message cannot be empty'}), 400
    
    # Create new group message
    new_message = GroupMessage(
        group_id=group_id,
        user_id=current_user.id,
        content=content
    )
    
    db.session.add(new_message)
    db.session.commit()
    
    # Build user badges
    user_badges = []
    if current_user.is_admin:
        user_badges.append({'icon': 'fas fa-crown', 'color': 'text-danger', 'title': 'Admin'})
    elif current_user.is_teacher:
        user_badges.append({'icon': 'fas fa-chalkboard-teacher', 'color': 'text-success', 'title': 'Teacher'})
    elif current_user.is_prefect:
        user_badges.append({'icon': 'fas fa-star', 'color': 'text-warning', 'title': 'Prefect'})
    
    if current_user.id == group.admin_id:
        user_badges.append({'icon': 'fas fa-crown', 'color': 'text-warning', 'title': 'Group Admin'})
    
    return jsonify({
        'success': True,
        'message': {
            'id': new_message.id,
            'content': new_message.content,
            'user_id': new_message.user_id,
            'user_name': current_user.full_name,
            'user_username': current_user.username,
            'user_initial': current_user.full_name[0].upper(),
            'user_badges': user_badges,
            'date_sent': new_message.date_sent.isoformat(),
            'timestamp': new_message.date_sent.timestamp(),
            'formatted_time': new_message.date_sent.strftime('%I:%M %p'),
            'is_edited': False,
            'can_edit': True,
            'can_report': False
        }
    })

@app.route('/edit_group_message/<int:message_id>', methods=['POST'])
def edit_group_message(message_id):
    """Edit a group message"""
    if 'user_id' not in session:
        flash('Please log in to edit messages.', 'error')
        return redirect(url_for('login'))
    
    message = GroupMessage.query.get_or_404(message_id)
    
    # Only the sender can edit their message
    if message.user_id != session['user_id']:
        flash('You can only edit your own messages.', 'error')
        return redirect(request.referrer or url_for('groups'))
    
    new_content = request.form.get('content', '').strip()
    if not new_content:
        flash('Message content cannot be empty.', 'error')
        return redirect(request.referrer or url_for('groups'))
    
    message.content = new_content
    message.is_edited = True
    message.date_edited = get_ist_now()
    db.session.commit()
    
    flash('Message edited successfully.', 'success')
    return redirect(request.referrer or url_for('groups'))

@app.route('/delete_group_message/<int:message_id>', methods=['POST'])
def delete_group_message(message_id):
    """Delete a group message"""
    if 'user_id' not in session:
        flash('Please log in to delete messages.', 'error')
        return redirect(url_for('login'))
    
    message = GroupMessage.query.get_or_404(message_id)
    
    # Only the sender can delete their message
    if message.user_id != session['user_id']:
        flash('You can only delete your own messages.', 'error')
        return redirect(request.referrer or url_for('groups'))
    
    db.session.delete(message)
    db.session.commit()
    
    flash('Message deleted successfully.', 'success')
    return redirect(request.referrer or url_for('groups'))

@app.route('/lost_and_found')
def lost_and_found():
    """View all lost and found items"""
    if 'user_id' not in session:
        flash('Please log in to access Lost and Found.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Disable lost and found for parents
    if current_user.is_parent:
        flash('Parents cannot access Lost and Found. This is a monitoring-only account.', 'warning')
        return redirect(url_for('parent_dashboard'))
    
    # Get all lost and found items, ordered by date reported (newest first)
    items = LostAndFound.query.order_by(LostAndFound.date_reported.desc()).all()
    
    # Get unread message count for navigation (if student)
    unread_count = 0
    if not session.get('is_admin'):
        unread_count = DirectMessage.query.filter_by(receiver_id=session['user_id'], is_read=False).count()
    
    return render_template('lost_and_found.html', items=items, unread_messages=unread_count)

@app.route('/report_lost_item', methods=['GET', 'POST'])
def report_lost_item():
    """Report a lost item"""
    if 'user_id' not in session:
        flash('Please log in to report a lost item.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        item_name = request.form['item_name'].strip()
        description = request.form['description'].strip()
        location_lost = request.form['location_lost'].strip()
        date_lost = request.form['date_lost']
        contact_info = request.form.get('contact_info', '').strip()
        
        if not all([item_name, description, location_lost, date_lost]):
            flash('Please fill in all required fields.', 'error')
            return render_template('report_lost_item.html')
        
        # Parse date
        try:
            from datetime import datetime
            date_lost_obj = datetime.strptime(date_lost, '%Y-%m-%d').date()
        except ValueError:
            flash('Invalid date format.', 'error')
            return render_template('report_lost_item.html')
        
        # Create new lost item report
        new_item = LostAndFound(
            user_id=session['user_id'],
            item_name=item_name,
            description=description,
            location_lost=location_lost,
            date_lost=date_lost_obj,
            contact_info=contact_info
        )
        
        db.session.add(new_item)
        db.session.commit()
        
        flash(f'Lost item "{item_name}" reported successfully!', 'success')
        return redirect(url_for('lost_and_found'))
    
    # Get unread message count for navigation (if student)
    unread_count = 0
    if not session.get('is_admin'):
        unread_count = DirectMessage.query.filter_by(receiver_id=session['user_id'], is_read=False).count()
    
    return render_template('report_lost_item.html', unread_messages=unread_count)

@app.route('/mark_as_found/<int:item_id>', methods=['POST'])
def mark_as_found(item_id):
    """Mark an item as found (Admin only)"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin access required.', 'error')
        return redirect(url_for('login'))
    
    item = LostAndFound.query.get_or_404(item_id)
    
    if item.is_found:
        flash('This item is already marked as found.', 'error')
        return redirect(url_for('lost_and_found'))
    
    found_location = request.form['found_location'].strip()
    found_date = request.form['found_date']
    
    if not all([found_location, found_date]):
        flash('Please provide both found location and date.', 'error')
        return redirect(url_for('lost_and_found'))
    
    # Parse date
    try:
        from datetime import datetime
        found_date_obj = datetime.strptime(found_date, '%Y-%m-%d').date()
    except ValueError:
        flash('Invalid date format.', 'error')
        return redirect(url_for('lost_and_found'))
    
    # Update item as found
    item.is_found = True
    item.found_location = found_location
    item.found_date = found_date_obj
    item.found_by_admin_id = session['user_id']
    
    db.session.commit()
    
    flash(f'Item "{item.item_name}" marked as found!', 'success')
    return redirect(url_for('lost_and_found'))

# Classwork and Homework routes (accessible by all users)

@app.route('/classwork')
def view_classwork():
    """View all classwork - accessible by all users"""
    if 'user_id' not in session:
        flash('Please log in to access classwork.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Get subject filter from query params
    subject_filter = request.args.get('subject', 'all')
    
    # Define available subjects
    subjects = ['All', 'Hindi', 'Maths', 'Science', 'French', 'Sanskrit', 'German', 'SST', 'Computer']
    
    # Get all classworks ordered by upload date
    classworks_query = Classwork.query.join(User)
    
    # Apply subject filter
    if subject_filter != 'all' and subject_filter in [s.lower() for s in subjects]:
        classworks_query = classworks_query.filter(Classwork.subject.ilike(f'%{subject_filter}%'))
    
    all_classworks = classworks_query.order_by(Classwork.upload_date.desc()).all()
    
    # For students, filter by visibility
    if current_user.is_student():
        classworks = []
        for cw in all_classworks:
            is_visible = cw.is_visible_to_student(current_user)
            logging.debug(f"Classwork '{cw.title}' visibility check for student {current_user.username} (Grade: {current_user.grade_level}, Section: {current_user.section}): "
                         f"Target grades: {cw.get_target_grades()}, Target sections: {cw.get_target_sections()}, Visible: {is_visible}")
            if is_visible:
                classworks.append(cw)
    else:
        classworks = all_classworks
    
    # For teachers, show teacher_classwork template with upload capability
    if current_user.is_teacher:
        # Teachers can see their own classwork and admin's classwork
        user_classworks_query = Classwork.query.join(User).filter(
            (Classwork.teacher_id == current_user.id) | (User.is_admin == True)
        )
        if subject_filter != 'all' and subject_filter in [s.lower() for s in subjects]:
            user_classworks_query = user_classworks_query.filter(Classwork.subject.ilike(f'%{subject_filter}%'))
        user_classworks = user_classworks_query.order_by(Classwork.upload_date.desc()).all()
        return render_template('teacher_classwork.html', classworks=user_classworks, subjects=subjects, 
                              current_subject=subject_filter, datetime=datetime)
    
    # For students and admins, show view-only template
    return render_template('view_classwork.html', classworks=classworks, subjects=subjects, 
                          current_subject=subject_filter, datetime=datetime)

@app.route('/homework')
def view_homework():
    """View all homework - accessible by all users"""
    if 'user_id' not in session:
        flash('Please log in to access homework.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Get subject filter from query params
    subject_filter = request.args.get('subject', 'all')
    
    # Define available subjects
    subjects = ['All', 'Hindi', 'Maths', 'Science', 'French', 'Sanskrit', 'German', 'SST', 'Computer']
    
    # Get all homework ordered by due date
    homework_query = Homework.query.join(User)
    
    # Apply subject filter
    if subject_filter != 'all' and subject_filter in [s.lower() for s in subjects]:
        homework_query = homework_query.filter(Homework.subject.ilike(f'%{subject_filter}%'))
    
    all_homework = homework_query.order_by(Homework.due_date.asc()).all()
    
    # For students, filter by visibility
    if current_user.is_student():
        homework_list = []
        for hw in all_homework:
            is_visible = hw.is_visible_to_student(current_user)
            logging.debug(f"Homework '{hw.title}' visibility check for student {current_user.username} (Grade: {current_user.grade_level}, Section: {current_user.section}): "
                         f"Target grades: {hw.get_target_grades()}, Target sections: {hw.get_target_sections()}, Visible: {is_visible}")
            if is_visible:
                homework_list.append(hw)
    else:
        homework_list = all_homework
    
    # For teachers, show teacher_homework template with creation capability
    if current_user.is_teacher:
        # Teachers can see their own homework and admin's homework
        user_homework_query = Homework.query.join(User).filter(
            (Homework.teacher_id == current_user.id) | (User.is_admin == True)
        )
        if subject_filter != 'all' and subject_filter in [s.lower() for s in subjects]:
            user_homework_query = user_homework_query.filter(Homework.subject.ilike(f'%{subject_filter}%'))
        user_homework = user_homework_query.order_by(Homework.due_date.asc()).all()
        return render_template('teacher_homework.html', homework_list=user_homework, subjects=subjects, 
                              current_subject=subject_filter, datetime=datetime)
    
    # For students and admins, show view-only template
    return render_template('view_homework.html', homework_list=homework_list, subjects=subjects, 
                          current_subject=subject_filter, datetime=datetime)

@app.route('/teacher/classwork')
def teacher_classwork():
    """Teacher classwork management page"""
    current_user = User.query.get(session['user_id']) if 'user_id' in session else None
    if not current_user or not (current_user.is_teacher or current_user.is_admin):
        flash('Access denied. Teacher or Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Get subject filter
    subject_filter = request.args.get('subject', 'all')
    subjects = ['All', 'Hindi', 'Maths', 'Science', 'French', 'Sanskrit', 'German', 'SST', 'Computer']
    
    # Show all classwork for admins, teachers can see their own and admin's classwork
    if current_user.is_admin:
        classworks_query = Classwork.query.join(User)
    else:
        # Teachers can see their own classwork and admin's classwork
        classworks_query = Classwork.query.join(User).filter(
            (Classwork.teacher_id == current_user.id) | (User.is_admin == True)
        )
    
    # Apply subject filter
    if subject_filter != 'all' and subject_filter in [s.lower() for s in subjects]:
        classworks_query = classworks_query.filter(Classwork.subject.ilike(f'%{subject_filter}%'))
    
    classworks = classworks_query.order_by(Classwork.upload_date.desc()).all()
    
    return render_template('teacher_classwork.html', classworks=classworks, subjects=subjects, 
                          current_subject=subject_filter, datetime=datetime)

@app.route('/teacher/classwork/upload', methods=['POST'])
def upload_classwork():
    """Upload PDF classwork file"""
    current_user = User.query.get(session['user_id']) if 'user_id' in session else None
    if not current_user or not (current_user.is_teacher or current_user.is_admin):
        flash('Access denied. Teacher or Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    if 'pdf_file' not in request.files:
        flash('No file selected.', 'error')
        return redirect(url_for('teacher_classwork'))
    
    file = request.files['pdf_file']
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('teacher_classwork'))
    
    if not file.filename.lower().endswith('.pdf'):
        flash('Only PDF files are allowed.', 'error')
        return redirect(url_for('teacher_classwork'))
    
    # Create uploads directory if it doesn't exist
    upload_dir = os.path.join(app.instance_path, 'uploads', 'classwork')
    os.makedirs(upload_dir, exist_ok=True)
    
    # Save file with secure filename
    filename = secure_filename(file.filename)
    timestamp = get_ist_now().strftime('%Y%m%d_%H%M%S_')
    filename = timestamp + filename
    file_path = os.path.join(upload_dir, filename)
    file.save(file_path)
    
    # Get target grades and sections
    target_grades = request.form.getlist('target_grades')
    target_sections = request.form.getlist('target_sections')
    
    # Convert to JSON for storage
    import json
    target_grades_json = json.dumps(target_grades) if target_grades else None
    target_sections_json = json.dumps(target_sections) if target_sections else None
    
    # Save to database
    classwork = Classwork(
        title=request.form['title'],
        description=request.form.get('description', ''),
        filename=filename,
        file_path=file_path,
        file_size=os.path.getsize(file_path),
        teacher_id=session['user_id'],
        subject=request.form.get('subject', ''),
        target_grades=target_grades_json,
        target_sections=target_sections_json
    )
    
    db.session.add(classwork)
    db.session.commit()
    
    flash('Classwork uploaded successfully!', 'success')
    return redirect(url_for('teacher_classwork'))

@app.route('/teacher/homework')
def teacher_homework():
    """Teacher homework management page"""
    current_user = User.query.get(session['user_id']) if 'user_id' in session else None
    if not current_user or not (current_user.is_teacher or current_user.is_admin):
        flash('Access denied. Teacher or Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Get subject filter
    subject_filter = request.args.get('subject', 'all')
    subjects = ['All', 'Hindi', 'Maths', 'Science', 'French', 'Sanskrit', 'German', 'SST', 'Computer']
    
    # Show all homework for admins, only own homework for teachers
    if current_user.is_admin:
        homework_query = Homework.query.join(User)
    else:
        homework_query = Homework.query.filter_by(teacher_id=current_user.id)
    
    # Apply subject filter
    if subject_filter != 'all' and subject_filter in [s.lower() for s in subjects]:
        homework_query = homework_query.filter(Homework.subject.ilike(f'%{subject_filter}%'))
    
    homework_list = homework_query.order_by(Homework.due_date.asc()).all()
    
    return render_template('teacher_homework.html', homework_list=homework_list, subjects=subjects, 
                          current_subject=subject_filter, datetime=datetime)

@app.route('/teacher/homework/create', methods=['POST'])
def create_homework():
    """Create new homework assignment"""
    current_user = User.query.get(session['user_id']) if 'user_id' in session else None
    if not current_user or not (current_user.is_teacher or current_user.is_admin):
        flash('Access denied. Teacher or Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    title = request.form['title']
    description = request.form['description']
    subject = request.form.get('subject', '')
    due_date_str = request.form['due_date']
    
    # Parse due date
    try:
        due_date = datetime.strptime(due_date_str, '%Y-%m-%dT%H:%M')
    except ValueError:
        flash('Invalid due date format.', 'error')
        return redirect(url_for('teacher_homework'))
    
    # Handle optional attachment
    attachment_filename = None
    attachment_path = None
    attachment_size = None
    
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file.filename != '':
            # Create uploads directory
            upload_dir = os.path.join(app.instance_path, 'uploads', 'homework')
            os.makedirs(upload_dir, exist_ok=True)
            
            # Save file
            filename = secure_filename(file.filename)
            timestamp = get_ist_now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename
            file_path = os.path.join(upload_dir, filename)
            file.save(file_path)
            
            attachment_filename = filename
            attachment_path = file_path
            attachment_size = os.path.getsize(file_path)
    
    # Get target grades and sections
    target_grades = request.form.getlist('target_grades')
    target_sections = request.form.getlist('target_sections')
    
    # Convert to JSON for storage
    import json
    target_grades_json = json.dumps(target_grades) if target_grades else None
    target_sections_json = json.dumps(target_sections) if target_sections else None
    
    # Save homework to database
    homework = Homework(
        title=title,
        description=description,
        subject=subject,
        due_date=due_date,
        teacher_id=session['user_id'],
        target_grades=target_grades_json,
        target_sections=target_sections_json,
        attachment_filename=attachment_filename,
        attachment_path=attachment_path,
        attachment_size=attachment_size
    )
    
    db.session.add(homework)
    db.session.commit()
    
    flash('Homework assignment created successfully!', 'success')
    return redirect(url_for('teacher_homework'))

@app.route('/download/classwork/<int:classwork_id>')
def download_classwork(classwork_id):
    """Download classwork PDF"""
    classwork = Classwork.query.get_or_404(classwork_id)
    
    # Check if file exists
    if not os.path.exists(classwork.file_path):
        flash('File not found.', 'error')
        return redirect(url_for('teacher_classwork'))
    
    from flask import send_file
    return send_file(classwork.file_path, as_attachment=True, 
                     download_name=classwork.filename)

@app.route('/download/homework/<int:homework_id>')
def download_homework_attachment(homework_id):
    """Download homework attachment"""
    homework = Homework.query.get_or_404(homework_id)
    
    if not homework.attachment_path or not os.path.exists(homework.attachment_path):
        flash('Attachment not found.', 'error')
        return redirect(url_for('teacher_homework'))
    
    from flask import send_file
    return send_file(homework.attachment_path, as_attachment=True,
                     download_name=homework.attachment_filename)

# Circular routes (accessible by all users)

@app.route('/circulars')
def view_circulars():
    """View all active circulars - accessible by all users"""
    if 'user_id' not in session:
        flash('Please log in to access circulars.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Get all active circulars ordered by date (newest first)
    circulars = Circular.query.filter(
        Circular.is_active == True,
        or_(Circular.expires_on.is_(None), Circular.expires_on > get_ist_now())
    ).join(User).order_by(Circular.date_published.desc()).all()
    
    # For teachers and admins, show management template
    if current_user.is_teacher or current_user.is_admin:
        user_circulars = Circular.query.filter_by(created_by_id=current_user.id)\
            .order_by(Circular.date_published.desc()).all()
        return render_template('manage_circulars.html', 
                             circulars=circulars, 
                             user_circulars=user_circulars,
                             datetime=datetime)
    
    # For students, show view-only template
    return render_template('view_circulars.html', circulars=circulars, datetime=datetime)

@app.route('/circular/create', methods=['POST'])
def create_circular():
    """Create new circular - only teachers and admins"""
    if 'user_id' not in session:
        flash('Please log in to create circulars.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    if not (current_user.is_teacher or current_user.is_admin):
        flash('Access denied. Teacher or admin privileges required.', 'error')
        return redirect(url_for('view_circulars'))
    
    title = request.form['title']
    content = request.form['content']
    category = request.form['category']
    priority = request.form.get('priority', 'Normal')
    expires_on_str = request.form.get('expires_on', '')
    
    # Parse expiry date if provided
    expires_on = None
    if expires_on_str:
        try:
            # Parse the date and set time to end of day (23:59:59)
            expires_on = datetime.strptime(expires_on_str, '%Y-%m-%d')
            expires_on = expires_on.replace(hour=23, minute=59, second=59)
        except ValueError:
            flash('Invalid expiry date format.', 'error')
            return redirect(url_for('view_circulars'))
    
    # Handle optional attachment
    attachment_filename = None
    attachment_path = None
    attachment_size = None
    
    if 'attachment' in request.files:
        file = request.files['attachment']
        if file.filename != '':
            # Create uploads directory
            upload_dir = os.path.join(app.instance_path, 'uploads', 'circulars')
            os.makedirs(upload_dir, exist_ok=True)
            
            # Save file
            filename = secure_filename(file.filename)
            timestamp = get_ist_now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename
            file_path = os.path.join(upload_dir, filename)
            file.save(file_path)
            
            attachment_filename = filename
            attachment_path = file_path
            attachment_size = os.path.getsize(file_path)
    
    # Save circular to database
    circular = Circular(
        title=title,
        content=content,
        category=category,
        priority=priority,
        expires_on=expires_on,
        created_by_id=session['user_id'],
        attachment_filename=attachment_filename,
        attachment_path=attachment_path,
        attachment_size=attachment_size
    )
    
    db.session.add(circular)
    db.session.commit()
    
    flash('Circular created successfully!', 'success')
    return redirect(url_for('view_circulars'))

@app.route('/download/circular/<int:circular_id>')
def download_circular_attachment(circular_id):
    """Download circular attachment"""
    circular = Circular.query.get_or_404(circular_id)
    
    if not circular.attachment_path or not os.path.exists(circular.attachment_path):
        flash('Attachment not found.', 'error')
        return redirect(url_for('view_circulars'))
    
    from flask import send_file
    return send_file(circular.attachment_path, as_attachment=True,
                     download_name=circular.attachment_filename)

@app.route('/circular/delete/<int:circular_id>')
def delete_circular(circular_id):
    """Delete circular - only creator, teachers and admins"""
    if 'user_id' not in session:
        flash('Please log in to delete circulars.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    circular = Circular.query.get_or_404(circular_id)
    
    # Check permissions: creator, admin, or teacher can delete
    if not (circular.created_by_id == current_user.id or current_user.is_admin or current_user.is_teacher):
        flash('Access denied. You can only delete your own circulars.', 'error')
        return redirect(url_for('view_circulars'))
    
    # Delete attachment file if exists
    if circular.attachment_path and os.path.exists(circular.attachment_path):
        os.remove(circular.attachment_path)
    
    db.session.delete(circular)
    db.session.commit()
    
    flash('Circular deleted successfully!', 'success')
    return redirect(url_for('view_circulars'))

# Calendar Routes
@app.route('/events/calendar')
def monthly_calendar():
    """Display monthly calendar - viewable by all users"""
    if 'user_id' not in session:
        flash('Please log in to access the calendar.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Get current month and year, or from request
    current_month = request.args.get('month', get_ist_now().strftime('%B'))
    current_year = int(request.args.get('year', get_ist_now().year))
    
    # Get calendar events for this month
    calendar_events = CalendarEvent.get_month_calendar(current_month, current_year)
    
    # Get days in month 
    days_in_month = CalendarEvent.get_days_in_month(current_month)
    
    # Month list for dropdown
    months = ['January', 'February', 'March', 'April', 'May', 'June',
              'July', 'August', 'September', 'October', 'November', 'December']
    
    # Can edit if admin
    can_edit = current_user.is_admin
    
    return render_template('monthly_calendar.html',
                         current_month=current_month,
                         current_year=current_year,
                         calendar_events=calendar_events,
                         days_in_month=days_in_month,
                         months=months,
                         can_edit=can_edit)

@app.route('/events/calendar/edit', methods=['POST'])
def edit_calendar():
    """Edit calendar events - admin only"""
    if 'user_id' not in session:
        flash('Please log in first.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('monthly_calendar'))
    
    month_name = request.form['month_name']
    year = int(request.form['year'])
    
    # Update calendar events for this month
    days_in_month = CalendarEvent.get_days_in_month(month_name)
    
    for day in range(1, days_in_month + 1):
        event_text = request.form.get(f'day_{day}', '').strip()
        
        # Find existing event or create new one
        existing_event = CalendarEvent.query.filter_by(
            month_name=month_name,
            year=year,
            day_number=day
        ).first()
        
        if existing_event:
            existing_event.event_text = event_text
            existing_event.last_updated = get_ist_now()
            existing_event.updated_by_id = current_user.id
        else:
            new_event = CalendarEvent(
                month_name=month_name,
                year=year,
                day_number=day,
                event_text=event_text,
                updated_by_id=current_user.id
            )
            db.session.add(new_event)
    
    db.session.commit()
    flash(f'Calendar updated successfully for {month_name} {year}!', 'success')
    return redirect(url_for('monthly_calendar', month=month_name, year=year))

# Announcements Routes
@app.route('/announcements')
def view_announcements():
    """View all announcements"""
    if 'user_id' not in session:
        flash('Please log in to view announcements.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Get all active announcements ordered by date
    announcements = Announcement.query.filter_by(is_active=True)\
        .order_by(Announcement.date_created.desc()).all()
    
    return render_template('announcements.html', 
                         announcements=announcements,
                         can_manage=current_user.is_admin,
                         datetime=datetime)

@app.route('/announcements/manage')
def manage_announcements():
    """Admin page to manage announcements"""
    if 'user_id' not in session:
        flash('Please log in first.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('view_announcements'))
    
    # Get all announcements (including inactive)
    announcements = Announcement.query.order_by(Announcement.date_created.desc()).all()
    
    return render_template('manage_announcements.html', 
                         announcements=announcements,
                         datetime=datetime)

@app.route('/announcements/create', methods=['POST'])
def create_announcement():
    """Create new announcement - admin only"""
    if 'user_id' not in session:
        flash('Please log in first.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('view_announcements'))
    
    title = request.form['title'].strip()
    content = request.form['content'].strip()
    
    if not title or not content:
        flash('Title and content are required.', 'error')
        return redirect(url_for('manage_announcements'))
    
    # Handle photo upload
    photo_filename = None
    photo_path = None
    photo_size = None
    
    if 'photo' in request.files:
        file = request.files['photo']
        if file.filename != '':
            # Check if it's an image
            allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
            file_ext = os.path.splitext(file.filename)[1].lower()
            
            if file_ext not in allowed_extensions:
                flash('Only image files are allowed (JPG, PNG, GIF, BMP, WEBP).', 'error')
                return redirect(url_for('manage_announcements'))
            
            # Create uploads directory
            upload_dir = os.path.join(app.instance_path, 'uploads', 'announcements')
            os.makedirs(upload_dir, exist_ok=True)
            
            # Save file with secure filename
            filename = secure_filename(file.filename)
            timestamp = get_ist_now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename
            file_path = os.path.join(upload_dir, filename)
            file.save(file_path)
            
            photo_filename = filename
            photo_path = file_path
            photo_size = os.path.getsize(file_path)
    
    # Create announcement
    announcement = Announcement(
        title=title,
        content=content,
        created_by_id=current_user.id,
        photo_filename=photo_filename,
        photo_path=photo_path,
        photo_size=photo_size
    )
    
    db.session.add(announcement)
    db.session.commit()
    
    flash('Announcement created successfully!', 'success')
    return redirect(url_for('view_announcements'))

@app.route('/announcements/delete/<int:announcement_id>')
def delete_announcement(announcement_id):
    """Delete announcement - admin only"""
    if 'user_id' not in session:
        flash('Please log in first.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('view_announcements'))
    
    announcement = Announcement.query.get_or_404(announcement_id)
    
    # Delete photo file if exists
    if announcement.photo_path and os.path.exists(announcement.photo_path):
        os.remove(announcement.photo_path)
    
    db.session.delete(announcement)
    db.session.commit()
    
    flash('Announcement deleted successfully!', 'success')
    return redirect(url_for('manage_announcements'))

@app.route('/announcements/photo/<int:announcement_id>')
def announcement_photo(announcement_id):
    """Serve announcement photo"""
    announcement = Announcement.query.get_or_404(announcement_id)
    
    if not announcement.photo_path or not os.path.exists(announcement.photo_path):
        return redirect(url_for('static', filename='images/placeholder.jpg')), 404
    
    from flask import send_file
    return send_file(announcement.photo_path)

# ============================
# REPORTING SYSTEM ROUTES
# ============================

@app.route('/report_content', methods=['POST'])
def report_content():
    """Report inappropriate content (posts, messages, group messages)"""
    if 'user_id' not in session:
        flash('Please log in to report content.', 'error')
        return redirect(url_for('login'))
    
    current_user = User.query.get(session['user_id'])
    
    # Only prefects and teachers can report content
    if not (current_user.is_prefect or current_user.is_teacher):
        flash('Access denied. Only prefects and teachers can report content.', 'error')
        return redirect(request.referrer or url_for('student_dashboard'))
    
    content_type = request.form.get('content_type')
    content_id = request.form.get('content_id')
    justification = request.form.get('justification', '').strip()
    
    if not content_type or not content_id or not justification:
        flash('All fields are required for reporting.', 'error')
        return redirect(request.referrer or url_for('student_dashboard'))
    
    # Get the reported user based on content type
    reported_user = None
    if content_type == 'post':
        content_obj = Post.query.get(content_id)
        if content_obj:
            reported_user = content_obj.author
    elif content_type == 'direct_message':
        content_obj = DirectMessage.query.get(content_id)
        if content_obj:
            reported_user = content_obj.sender
    elif content_type == 'group_message':
        content_obj = GroupMessage.query.get(content_id)
        if content_obj:
            reported_user = content_obj.user
    
    if not reported_user:
        flash('Content not found.', 'error')
        return redirect(request.referrer or url_for('student_dashboard'))
    
    # Check if current user can report this user based on hierarchy
    if not current_user.can_report_or_delete_user(reported_user):
        flash('Access denied. You can only report students of your grade level or below.', 'error')
        return redirect(request.referrer or url_for('student_dashboard'))
    
    # Check if already reported
    existing_report = Report.query.filter_by(
        content_type=content_type,
        content_id=content_id,
        status='pending'
    ).first()
    
    if existing_report:
        flash('This content has already been reported and is pending review.', 'warning')
        return redirect(request.referrer or url_for('student_dashboard'))
    
    # Create new report
    new_report = Report(
        reporter_id=current_user.id,
        reported_user_id=reported_user.id,
        content_type=content_type,
        content_id=int(content_id),
        justification=justification
    )
    
    db.session.add(new_report)
    db.session.commit()
    
    flash('Content reported successfully. Admin will review it soon.', 'success')
    return redirect(request.referrer or url_for('student_dashboard'))

@app.route('/admin/reports')
def admin_reports():
    """Admin panel for viewing and managing reports"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    # Get all reports with related data
    reports = Report.query.options(
        joinedload(Report.reporter),
        joinedload(Report.reported_user),
        joinedload(Report.reviewed_by)
    ).order_by(Report.date_reported.desc()).all()
    
    return render_template('admin_reports.html', reports=reports)

@app.route('/admin/reports/<int:report_id>/review', methods=['POST'])
def review_report(report_id):
    """Admin review and approve/reject a report with points adjustment"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    report = Report.query.get_or_404(report_id)
    
    if report.status != 'pending':
        flash('This report has already been reviewed.', 'warning')
        return redirect(url_for('admin_reports'))
    
    action = request.form.get('action')  # 'approve' or 'reject'
    admin_notes = request.form.get('admin_notes', '').strip()
    points_adjustment = request.form.get('points_adjustment', 0, type=int)
    
    if action not in ['approve', 'reject']:
        flash('Invalid action.', 'error')
        return redirect(url_for('admin_reports'))
    
    # Update report
    report.status = 'approved' if action == 'approve' else 'rejected'
    report.date_reviewed = get_ist_now()
    report.reviewed_by_id = session['user_id']
    report.admin_notes = admin_notes
    report.points_adjustment = points_adjustment if action == 'approve' else 0
    
    # Apply points adjustment if approved
    ban_duration = request.form.get('ban_duration', 0, type=int)  # Duration in hours
    ban_reason = request.form.get('ban_reason', '').strip()
    
    if action == 'approve' and points_adjustment != 0:
        report.reported_user.points += points_adjustment
        # Ensure points don't go below 0
        if report.reported_user.points < 0:
            report.reported_user.points = 0
    
    # Apply limitation if points are zero and limitation is requested
    if action == 'approve' and report.reported_user.points == 0 and ban_duration > 0 and ban_reason:
        admin_user = User.query.get(session['user_id'])
        report.reported_user.limit_user(ban_reason, ban_duration, admin_user)
    
    db.session.commit()
    
    action_text = 'approved' if action == 'approve' else 'rejected'
    points_text = f" Points adjusted by {points_adjustment}." if action == 'approve' and points_adjustment != 0 else ""
    limit_text = f" User limited for {ban_duration} hours." if action == 'approve' and ban_duration > 0 and ban_reason else ""
    flash(f'Report {action_text} successfully.{points_text}{limit_text}', 'success')
    
    return redirect(url_for('admin_reports'))

@app.route('/admin/reset_all_points', methods=['POST'])
def reset_all_points():
    """DANGEROUS: Reset all users' points to 12"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    # Confirm with admin
    confirm = request.form.get('confirm')
    if confirm != 'RESET_ALL_POINTS':
        flash('Points reset cancelled. You must type "RESET_ALL_POINTS" to confirm.', 'error')
        return redirect(url_for('admin_reports'))
    
    # Reset all points to 12
    User.query.update({User.points: 12})
    db.session.commit()
    
    flash('⚠️ ALL USER POINTS HAVE BEEN RESET TO 12! This action cannot be undone.', 'warning')
    return redirect(url_for('admin_reports'))

@app.route('/admin/manage_points')
def admin_manage_points():
    """Admin panel for managing user points and bans"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    # Get all students (non-admin, non-teacher, non-parent users)
    students = User.query.filter(
        User.is_admin == False,
        User.is_teacher == False,
        User.is_parent == False
    ).order_by(User.grade_level, User.section, User.full_name).all()
    
    return render_template('admin_manage_points.html', students=students)

@app.route('/admin/adjust_points/<int:user_id>', methods=['POST'])
def adjust_points(user_id):
    """Manually adjust a user's points"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    points_change = request.form.get('points_change', 0, type=int)
    reason = request.form.get('reason', '').strip()
    
    if not reason:
        flash('Reason is required for point adjustments.', 'error')
        return redirect(url_for('admin_manage_points'))
    
    old_points = user.points
    user.points += points_change
    
    # Ensure points don't go below 0
    if user.points < 0:
        user.points = 0
    
    db.session.commit()
    
    flash(f'Points adjusted for {user.full_name}: {old_points} → {user.points} (Reason: {reason})', 'success')
    return redirect(url_for('admin_manage_points'))

@app.route('/admin/limit_user/<int:user_id>', methods=['POST'])
def limit_user_admin(user_id):
    """Limit a user with optional duration"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    limit_reason = request.form.get('limit_reason', '').strip()
    limit_duration = request.form.get('limit_duration', 0, type=int)  # hours
    
    if not limit_reason:
        flash('Limitation reason is required.', 'error')
        return redirect(url_for('admin_manage_points'))
    
    admin_user = User.query.get(session['user_id'])
    user.limit_user(limit_reason, limit_duration if limit_duration > 0 else None, admin_user)
    
    duration_text = f" for {limit_duration} hours" if limit_duration > 0 else " permanently"
    flash(f'{user.full_name} has been limited{duration_text}. Reason: {limit_reason}', 'warning')
    return redirect(url_for('admin_manage_points'))

@app.route('/admin/unlimit_user/<int:user_id>', methods=['POST'])
def unlimit_user_admin(user_id):
    """Unlimit a user"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('login'))
    
    user = User.query.get_or_404(user_id)
    user.unlimit_user()
    
    flash(f'{user.full_name} limitation has been removed.', 'success')
    return redirect(url_for('admin_manage_points'))

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors"""
    return render_template('login.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    db.session.rollback()
    return render_template('login.html'), 500

