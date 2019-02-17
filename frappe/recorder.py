# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and Contributors
# MIT License. See license.txt
from __future__ import unicode_literals

import json
import time
import traceback
import frappe
import sqlparse
import datetime


def sql(*args, **kwargs):
	# Execute wrapped function as is
	# Record arguments as well as return value
	# Record start and end time as well
	start_time = time.time()
	result = frappe.db._sql(*args, **kwargs)
	end_time = time.time()

	stack = "".join(traceback.format_stack())

	# Big hack here
	# PyMysql stores exact DB query in cursor._executed
	# Assumes that function refers to frappe.db.sql
	# __self__ will refer to frappe.db
	# Rest is trivial
	query = frappe.db._cursor._executed
	query = sqlparse.format(query.strip(), keyword_case="upper", reindent=True)

	data = {
		"query": query,
		"stack": stack,
		"time": start_time,
		"duration": float("{:.3f}".format((end_time - start_time) * 1000)),
	}

	# Record all calls, Will be later stored in cache
	frappe.local._recorder.register(data)
	return result


def record():
	if frappe.cache().get("recorder-intercept"):
		frappe.local._recorder = Recorder()


def dump():
	if hasattr(frappe.local, "_recorder"):
		frappe.local._recorder.dump()
		frappe.publish_realtime(event="recorder-dump-event")


class Recorder():
	def __init__(self):
		self.id = frappe.generate_hash(length=10)
		self.time = datetime.datetime.now()
		self.calls = []
		self.path = frappe.request.path
		self.cmd = frappe.local.form_dict.cmd or ""
		self.method = frappe.request.method

		self.request = {
			"headers": dict(frappe.local.request.headers),
			"data": frappe.local.form_dict,
		}
		_patch()

	def register(self, data):
		self.calls.append(data)

	def dump(self):
		request_data = {
			"id": self.id,
			"path": self.path,
			"cmd": self.cmd,
			"time": self.time,
			"queries": len(self.calls),
			"time_queries": float("{:0.3f}".format(sum(call["duration"] for call in self.calls))),
			"duration": float("{:0.3f}".format((datetime.datetime.now() - self.time).total_seconds() * 1000)),
			"method": self.method,
		}
		frappe.cache().lpush("recorder-requests", json.dumps(request_data, default=str))

		request_data["calls"] = self.calls
		request_data["http"] = self.request
		frappe.cache().set("recorder-request-{}".format(self.id), json.dumps(request_data, default=str))


def _patch():
	frappe.db._sql = frappe.db.sql
	frappe.db.sql = sql


def compress(data):
	if data:
		if isinstance(data[0], dict):
			keys = list(data[0].keys())
			values = list()
			for row in data:
				values.append([row.get(key) for key in keys])
		else:
			keys = [column[0] for column in frappe.db._cursor.description]
			values = data
	else:
		keys, values = [], []
	return {"keys": keys, "values": values}


def do_not_record(function):
	def wrapper(*args, **kwargs):
		if hasattr(frappe.local, "_recorder"):
			del frappe.local._recorder
			frappe.db.sql = frappe.db._sql
		return function(*args, **kwargs)
	return wrapper


@frappe.whitelist()
@do_not_record
def get_status(*args, **kwargs):
	if frappe.cache().get("recorder-intercept"):
		return {"status": "Active", "color": "green"}
	return {"status": "Inactive", "color": "red"}


@frappe.whitelist()
@do_not_record
def set_recorder_state(should_record, *args, **kwargs):
	if should_record == "true":
		frappe.cache().set("recorder-intercept", 1)
		return {"status": "Active", "color": "green"}
	else:
		frappe.cache().delete("recorder-intercept")
		return {"status": "Inactive", "color": "red"}


@frappe.whitelist()
@do_not_record
def get(id=None, *args, **kwargs):
	if id:
		result = json.loads(frappe.cache().get("recorder-request-{}".format(id)).decode())
	else:
		requests = frappe.cache().lrange("recorder-requests", 0, -1)
		result = list(map(lambda request: json.loads(request.decode()), requests))
	return result


@frappe.whitelist()
@do_not_record
def delete(*args, **kwargs):
	frappe.cache().delete_value("recorder-requests")
