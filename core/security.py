from django.contrib.sessions.models import Session
from django.utils import timezone


def invalidate_user_sessions(user, keep_key=None):
    """
    Log a user out everywhere (password reset, account deactivation).
    Simple version: fine for a campus-sized deployment. For very large session
    tables, store the user id on the session row instead.
    """
    for session in Session.objects.filter(expire_date__gte=timezone.now()):
        if keep_key and session.session_key == keep_key:
            continue
        if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
            session.delete()
