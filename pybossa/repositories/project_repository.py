# -*- coding: utf8 -*-
# This file is part of PYBOSSA.
#
# Copyright (C) 2015 Scifabric LTD.
#
# PYBOSSA is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PYBOSSA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with PYBOSSA.  If not, see <http://www.gnu.org/licenses/>.

from sqlalchemy.exc import IntegrityError
from sqlalchemy import cast, Date
from sqlalchemy.sql import text

from pybossa.repositories import Repository
from pybossa.model.project import Project
from pybossa.model.category import Category
from pybossa.exc import WrongObjectError, DBIntegrityError
from pybossa.cache import projects as cached_projects
from pybossa.core import uploader
from werkzeug.exceptions import BadRequest


class ProjectRepository(Repository):

    # Methods for Project objects
    def get(self, id):
        return self.db.session.query(Project).get(id)

    def get_by_shortname(self, short_name):
        return self.db.session.query(Project).filter_by(short_name=short_name).first()

    def get_by(self, **attributes):
        return self.db.session.query(Project).filter_by(**attributes).first()

    def get_all(self):
        return self.db.session.query(Project).all()

    def filter_by(self, limit=None, offset=0, yielded=False, last_id=None,
                  fulltextsearch=None, desc=False, **filters):
        if filters.get('owner_id'):
            filters['owner_id'] = filters.get('owner_id')
        return self._filter_by(Project, limit, offset, yielded, last_id,
                               fulltextsearch, desc, **filters)

    def save(self, project):
        self._validate_can_be('saved', project)
        self._empty_strings_to_none(project)
        self._creator_is_owner(project)
        self._verify_has_password(project)
        try:
            self.db.session.add(project)
            self.db.session.commit()
        except IntegrityError as e:
            self.db.session.rollback()
            raise DBIntegrityError(e)

    def update(self, project):
        self._validate_can_be('updated', project)
        self._empty_strings_to_none(project)
        self._creator_is_owner(project)
        self._verify_has_password(project)
        try:
            self.db.session.merge(project)
            self.db.session.commit()
        except IntegrityError as e:
            self.db.session.rollback()
            raise DBIntegrityError(e)

    def delete(self, project):
        self._validate_can_be('deleted', project)
        project = self.db.session.query(Project).filter(Project.id==project.id).first()
        self.db.session.delete(project)
        self.db.session.commit()
        cached_projects.clean(project.id)
        self._delete_zip_files_from_store(project)


    # Methods for Category objects
    def get_category(self, id=None):
        if id is None:
            return self.db.session.query(Category).first()
        return self.db.session.query(Category).get(id)

    def get_category_by(self, **attributes):
        return self.db.session.query(Category).filter_by(**attributes).first()

    def get_all_categories(self):
        return self.db.session.query(Category).all()

    def filter_categories_by(self, limit=None, offset=0, yielded=False,
                             last_id=None, fulltextsearch=None,
                             orderby='id',
                             desc=False, **filters):
        if filters.get('owner_id'):
            del filters['owner_id']
        return self._filter_by(Category, limit, offset, yielded, last_id,
                               fulltextsearch, desc, orderby, **filters)

    def save_category(self, category):
        self._validate_can_be('saved as a Category', category, klass=Category)
        try:
            self.db.session.add(category)
            self.db.session.commit()
        except IntegrityError as e:
            self.db.session.rollback()
            raise DBIntegrityError(e)

    def update_category(self, new_category, caller="web"):
        self._validate_can_be('updated as a Category', new_category, klass=Category)
        try:
            self.db.session.merge(new_category)
            self.db.session.commit()
        except IntegrityError as e:
            self.db.session.rollback()
            raise DBIntegrityError(e)

    def delete_category(self, category):
        self._validate_can_be('deleted as a Category', category, klass=Category)
        self.db.session.query(Category).filter(Category.id==category.id).delete()
        self.db.session.commit()

    def _empty_strings_to_none(self, project):
        if project.name == '':
            project.name = None
        if project.short_name == '':
            project.short_name = None
        if project.description == '':
            project.description = None

    def _creator_is_owner(self, project):
        if project.owners_ids is None:
            project.owners_ids = []
        if project.owner_id not in project.owners_ids:
            project.owners_ids.append(project.owner_id)

    def _verify_has_password(self, project):
        if not project.info.get('passwd_hash'):
            raise BadRequest('Project must have a password')

    def _validate_can_be(self, action, element, klass=Project):
        if not isinstance(element, klass):
            name = element.__class__.__name__
            msg = '%s cannot be %s by %s' % (name, action, self.__class__.__name__)
            raise WrongObjectError(msg)

    def _delete_zip_files_from_store(self, project):
        from pybossa.core import json_exporter, csv_exporter
        global uploader
        if uploader is None:
            from pybossa.core import uploader
        json_tasks_filename = json_exporter.download_name(project, 'task')
        csv_tasks_filename = csv_exporter.download_name(project, 'task')
        json_taskruns_filename = json_exporter.download_name(project, 'task_run')
        csv_taskruns_filename = csv_exporter.download_name(project, 'task_run')
        container = "user_%s" % project.owner_id
        uploader.delete_file(json_tasks_filename, container)
        uploader.delete_file(csv_tasks_filename, container)
        uploader.delete_file(json_taskruns_filename, container)
        uploader.delete_file(csv_taskruns_filename, container)

    def get_projects_report(self):
        sql = text(
            '''
            WITH completed_tasks AS
              (
                 SELECT
                    task.project_id,
                    COUNT(DISTINCT task.id) AS value,
                    MAX(task_run.finish_time) AS ft
                 FROM task INNER JOIN task_run on task.id = task_run.task_id
                 WHERE task.state = 'completed'
                 GROUP BY task.project_id
              ), all_tasks AS
              (
                 SELECT
                    project_id,
                    COUNT(task.id) AS value
                 FROM task
                 GROUP BY project_id
              ), workers AS
              (
                 SELECT DISTINCT
                    project_id,
                    user_id,
                    "user".fullname,
                    "user".email_addr
                 FROM task_run INNER JOIN "user" ON task_run.user_id = "user".id
              ), n_workers AS
              (
                 SELECT
                    project_id,
                    COUNT(user_id) as value
                 FROM workers
                 GROUP BY project_id
              ),
              n_taskruns AS (
                 SELECT project_id,
                        COUNT(id) AS value
                 FROM task_run
                 GROUP BY project_id
              ),
              pending_taskruns AS (
                 SELECT project_id,
                        SUM(task.n_answers - COALESCE(t.actual_answers, 0)) AS value
                 FROM task
                 LEFT JOIN (
                    SELECT task_id,
                           COUNT(id) AS actual_answers
                    FROM task_run
                    GROUP BY task_id) AS t
                 ON task.id = t.task_id
                 WHERE task.state = 'ongoing'
                 GROUP BY project_id
              )
              SELECT
                 project.id,
                 project.name,
                 project.short_name,
                 project.description,
                 project.long_description,
                 project.created,
                 u.name as owner_name,
                 u.email_addr as owner_email,
                 category.name as category_name,
                 project.allow_anonymous_contributors,
                 (
                    COALESCE(project.info::json ->> 'passwd_hash', 'null') != 'null'
                 )
                 as password_protected,
                 project.webhook,
                 COALESCE(project.info::json ->> 'sched', 'default') as scheduler,
                 completed_tasks.ft,
                 CASE
                    WHEN
                       all_tasks.value = 0
                       OR completed_tasks.value IS NULL
                    THEN
                       0
                    ELSE
                       completed_tasks.value * 100 / all_tasks.value
                 END
                 as percent_complete,
                 COALESCE(all_tasks.value, 0) AS n_tasks,
                 COALESCE(all_tasks.value, 0) - COALESCE(completed_tasks.value, 0) AS pending_tasks,
                 COALESCE(n_workers.value, 0) as n_workers,
                 (
                    SELECT
                       n_answers
                    FROM
                       task
                    WHERE
                       project_id = project.id
                    ORDER BY
                       task.id DESC LIMIT 1
                 )
                 as n_answers,
                 (
                    SELECT
                       string_agg(concat('(', workers.user_id, ';', workers.fullname, ';', workers.email_addr, ')'), '|')
                    FROM
                       workers
                    WHERE
                       project.id = workers.project_id
                 )
                 as workers,
                 project.updated,
                 COALESCE((
                    SELECT created
                    FROM task
                    WHERE project_id = project.id
                    AND state != 'completed'
                    ORDER BY priority_0 DESC, created ASC
                    LIMIT 1
                 ), 'null') AS oldest_available,
                 COALESCE((
                    SELECT MAX(finish_time)
                    FROM task_run
                    WHERE project_id = project.id
                 ), 'null') AS last_submission,
                 COALESCE(n_taskruns.value, 0) AS n_taskruns,
                 COALESCE(pending_taskruns.value, 0) AS pending_taskruns
              FROM
                 project
                 INNER JOIN
                    "user" as u
                    on project.owner_id = u.id
                 INNER JOIN
                    category
                    on project.category_id = category.id
                 LEFT OUTER JOIN
                    completed_tasks
                    ON project.id = completed_tasks.project_id
                 LEFT OUTER JOIN
                    all_tasks
                    ON project.id = all_tasks.project_id
                 LEFT OUTER JOIN
                    n_workers
                    ON project.id = n_workers.project_id
                 LEFT OUTER JOIN
                    n_taskruns
                    ON project.id = n_taskruns.project_id
                 LEFT OUTER JOIN
                    pending_taskruns
                    ON project.id = pending_taskruns.project_id;
            '''
        )

        return self.db.session.execute(sql)
