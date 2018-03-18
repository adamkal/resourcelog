import os
import datetime
import click
import json
import requests
import lassie
from pathlib import Path
import gevent
from pendulum import Pendulum
from flask import (Flask, render_template, request, url_for, redirect, abort,
                   g, jsonify)
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'RESOURCELOG_DB_URL', 'sqlite:///.data/test.db')
db = SQLAlchemy(app)

SOURCES_PATH = os.environ.get('RESOURCELOG_SOURCES', '.data/sources.json')
WEBHOOKS = os.environ.get('RESOURCELOG_WEBHOOKS', '.data/webhooks.json')


def get_source(token):
    if not hasattr(g, 'sources'):
        sources_path = Path(SOURCES_PATH)
        with sources_path.open() as f:
            g.sources = json.load(f)
    return g.sources[token]


def get_webhooks():
    if not hasattr(g, 'webhooks'):
        sources_path = Path(WEBHOOKS)
        with sources_path.open() as f:
            g.webhooks = json.load(f)
    return g.webhooks


class Resource(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(1024), unique=True, nullable=False)
    source = db.Column(db.String(120), nullable=True)
    description = db.Column(db.Text(), nullable=True)
    title = db.Column(db.String(500), nullable=True)

    created_at = db.Column(db.DateTime(), nullable=False,
                           default=datetime.datetime.utcnow)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            info = lassie.fetch(self.url)
        except lassie.LassieError:
            pass
        else:
            self.title = info.get('title')
            self.description = info.get('description')

    def __repr__(self):
        return '<Resource %r>' % self.url


@app.cli.command()
def init_db():
    db.drop_all()
    db.create_all()
    click.echo(click.style('OK', fg='green'))


@app.route('/')
def home():
    return render_template(
        'home.html',
        resources=Resource.query.order_by(Resource.created_at.desc()).all())


@app.route('/resources/', methods=['POST'])
def add_resource():
    if request.is_json:
        try:
            token = request.json['token']
            url = request.json['url']
            source = get_source(token)
        except KeyError:
            abort(400)
        app.logger.info('Received {} from {}'.format(url, source))
        if Resource.query.filter_by(url=url).one_or_none():
            return jsonify({
                "status": "duplicate",
                "message": "Yeah! This one is good! I already have it. Thx!"
            })
        consume_resource(url, source)
        return jsonify({"status": "accepted", "message": "Thx!"})
    else:
        url = request.form['url']
        consume_resource(url)
        return redirect(url_for('home'))


@app.template_filter('humanize_date')
def humanize_date(value):
    return Pendulum.instance(value).diff_for_humans()


def consume_resource(url, source=None):
    resource = Resource(url=url, source=source)
    app.logger.info(resource)
    db.session.add(resource)
    db.session.commit()

    for target_url, token in get_webhooks().items():
        app.logger.info('Sending {} to {} '.format(url, target_url))
        requests.post(target_url, json={
            'token': token,
            'url': url
        }, timeout=2)
