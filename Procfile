release: python manage.py migrate --noinput
web: gunicorn vanthir.wsgi --bind 0.0.0.0:$PORT --workers 3
