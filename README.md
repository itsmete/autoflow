# AutoFlow

An REST API for businesses to manage their automations and workflows.

## Technologies
- Django 5.0 + Restful Framework
- Postgresql
- Celery + Redis
- JWT Authentication

## Setup

**For OSX and Linux**
```bash
git clone https://github.com/itsmete/autoflow
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```



## Architecture

- Multi-Tenant structure
- Role-based authorisation (SuperAdmin -> Owner -> Branch Manager -> Staff)
- Plugin based automation engine (Trigger -> Condition -> Action)

## API Documentation
Swagger : http:localhost:8000/api/docs/

