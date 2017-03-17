#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2013-2016 DNAnexus, Inc.
#
# This file is part of dx-toolkit (DNAnexus platform client libraries).
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may not
#   use this file except in compliance with the License. You may obtain a copy
#   of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.

from __future__ import print_function, unicode_literals, division, absolute_import

import os, sys, unittest, json, tempfile, subprocess, csv, shutil, re, base64, random, time
import filecmp
import pipes
import stat
import hashlib
import collections
import string
from contextlib import contextmanager
import pexpect
import requests

import dxpy
from dxpy.scripts import dx_build_app
from dxpy_testutil import (DXTestCase, DXTestCaseBuildApps, check_output, temporary_project,
                           select_project, cd, override_environment, generate_unique_username_email,
                           without_project_context, without_auth, as_second_user, chdir, run, DXCalledProcessError)
import dxpy_testutil as testutil

CACHE_DIR = '/tmp/dx-docker-cache'

def create_file_in_project(fname, trg_proj_id, folder=None):
    data = "foo"
    if folder is None:
        dxfile = dxpy.upload_string(data, name=fname, project=trg_proj_id, wait_on_close=True)
    else:
        dxfile = dxpy.upload_string(data, name=fname, project=trg_proj_id, folder=folder, wait_on_close=True)
    return dxfile.get_id()


def create_project():
    project_name = "test_dx_cp_" + str(random.randint(0, 1000000)) + "_" + str(int(time.time() * 1000))
    return dxpy.api.project_new({'name': project_name})['id']


def rm_project(proj_id):
    dxpy.api.project_destroy(proj_id, {"terminateJobs": True})


def create_folder_in_project(proj_id, path):
    dxpy.api.project_new_folder(proj_id, {"folder": path})

def list_folder(proj_id, path):
    output = dxpy.api.project_list_folder(proj_id, {"folder": path})
    # Canonicalize to account for possibly different ordering
    output['folders'] = set(output['folders'])
    # (objects is a list of dicts-- which are not hashable-- so just
    # sort them to canonicalize instead of putting them in a set)
    output['objects'] = sorted(output['objects'])
    return output
@unittest.skipUnless(testutil.TEST_DX_DOCKER,
                         'skipping tests that would run dx-docker')
class TestDXDocker(DXTestCase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(CACHE_DIR)

    def test_dx_docker_pull(self):
        run("dx-docker pull ubuntu:14.04")
        self.assertTrue(os.path.isfile(os.path.join(CACHE_DIR, 'ubuntu%3A14.04.aci')))
        run("dx-docker pull ubuntu:15.04")
        self.assertTrue(os.path.isfile(os.path.join(CACHE_DIR, 'ubuntu%3A15.04.aci')))
    
    def test_dx_docker_pull_silent(self):
        dx_docker_out = run("dx-docker pull -q busybox").strip()
        self.assertEqual(dx_docker_out, '')

    def test_dx_docker_pull_quay(self):
        run("dx-docker pull quay.io/ucsc_cgl/samtools")
        self.assertTrue(os.path.isfile(os.path.join(CACHE_DIR, 'quay.io%2Fucsc_cgl%2Fsamtools.aci')))

    def test_dx_docker_pull_hash_or_not(self):
        run("dx-docker pull geetduggal/testdocker")
        self.assertTrue(os.path.isfile(os.path.join(CACHE_DIR, 'geetduggal%2Ftestdocker.aci')))
        run("dx-docker pull geetduggal/testdocker@sha256:b680a129fdd06380c461c3b97240a61c246328c6917d60aa3eb393e49529ac9c")
        self.assertTrue(os.path.isfile(os.path.join(CACHE_DIR, 'geetduggal%2Ftestdocker%40sha256%3Ab680a129fdd06380c461c3b97240a61c246328c6917d60aa3eb393e49529ac9c.aci')))

    def test_dx_docker_pull_failure(self):
        with self.assertSubprocessFailure(exit_code=1, stderr_regexp='Failed to obtain image'):
            run("dx-docker pull busyboxasdf")

    def test_dx_docker_basic_commands(self):
        run("dx-docker run ubuntu:14.04 ls --color")
        run("dx-docker run ubuntu:15.04 ls")

    def test_dx_docker_run_from_hash(self):
        run("dx-docker run geetduggal/testdocker@sha256:b680a129fdd06380c461c3b97240a61c246328c6917d60aa3eb393e49529ac9c")

    def test_dx_docker_run_error_codes(self):
        with self.assertSubprocessFailure(exit_code=1):
            run("dx-docker run ubuntu:14.04 false")
        run("dx-docker run ubuntu:14.04 true")

    def test_dx_docker_volume(self):
        os.makedirs('dxdtestdata')
        run("dx-docker run -v dxdtestdata:/data-host ubuntu:14.04 touch /data-host/newfile.txt")
        self.assertTrue(os.path.isfile(os.path.join('dxdtestdata', 'newfile.txt')))
        shutil.rmtree('dxdtestdata')

    def test_dx_docker_entrypoint_cmd(self):
        docker_out = run("docker run geetduggal/testdocker /bin")
        dx_docker_out = run("dx-docker run -q geetduggal/testdocker /bin")
        self.assertEqual(docker_out, dx_docker_out)

    def test_dx_docker_home_dir(self):
        run("dx-docker run julia:0.5.0 julia -E 'println(\"hello world\")'")

    def test_dx_docker_run_rm(self):
        run("dx-docker run --rm ubuntu ls")

    def test_dx_docker_run_canonical(self):
        run("dx-docker run quay.io/ucsc_cgl/samtools --help")

    def test_dx_docker_add_to_applet(self):
        os.makedirs('tmpapp')
        run("docker pull busybox")
        with self.assertSubprocessFailure(exit_code=1, stderr_regexp='does not appear to have a dxapp.json that parses'):
            run("dx-docker add-to-applet busybox tmpapp")
        with open('tmpapp/dxapp.json', 'w') as dxapp:
            dxapp.write("[]")
        run("dx-docker add-to-applet busybox tmpapp")
        self.assertTrue(os.path.isfile(os.path.join('tmpapp', 'resources/tmp/dx-docker-cache/busybox.aci')))
        shutil.rmtree('tmpapp')

    def test_dx_docker_create_asset(self):
        with temporary_project(select=True) as temp_project:
            test_projectid = temp_project.get_id()
            run("docker pull ubuntu:14.04")
            run("dx-docker create-asset ubuntu:14.04")
            self.assertEqual(run("dx ls ubuntu\\\\:14.04").strip(), 'ubuntu:14.04')

            create_folder_in_project(test_projectid, '/testfolder')
            run("dx-docker create-asset busybox -o testfolder")

            ls_out = run("dx ls /testfolder").strip()
            self.assertEqual(ls_out, 'busybox')

            ls_out = run("dx ls testfolder\\/busybox.tar.gz")
            self.assertEqual(ls_out, 'busybox.tar.gz')

    def test_dx_docker_additional_container(self):
        run("dx-docker run busybox ls")

    def test_dx_docker_working_dir_override(self):
        run("dx-docker run -v $PWD:/tmp -w /tmp quay.io/ucsc_cgl/samtools faidx test.fa")
