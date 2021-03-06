# -*- encoding: utf-8 -*-
'''
@Description: 
@Date       : 2021/08/12 15:36:03
@Author     : apr
'''

import os, subprocess, json, logging, abc, shutil
from unidiff import PatchSet
from collections import OrderedDict

from utils import Yaml_util, File_util
import Config

logger = logging.getLogger()


class Dataset(object, metaclass=abc.ABCMeta):
    def __init__(self, name):
        self.project_data = {}
        self.bugs = None
        self.bugs = self.get_bugs()
        self.name = name
        logger.info(f"{self.name} is created.")

    @abc.abstractclassmethod
    def get_bugs(self):
        pass

    def clean_dir(self, bug, working_dir):
        # if os.path.exists(output_dir):
        #     logger.info(f"clean output dir: {output_dir}")
        #     File_util.rm_all_content_in_dir(output_dir)
        bug_dir = os.path.join(working_dir, bug.name)
        if os.path.exists(bug_dir):
            logger.info(f"clean output dir: {bug_dir}")
            File_util.rm_all_content_in_dir(bug_dir)

    def checkout_and_compile(self, bug, working_dir, bug_pool_dir):
        if os.path.exists(bug_pool_dir) and len(os.listdir(bug_pool_dir)) > 0:
            logger.info("Checkout is already done, just copy the bug dir.")
            self.set_bug_working_dir(bug, working_dir)

            File_util.rm_dir(working_dir)
            shutil.copytree(bug_pool_dir, working_dir)
        else:
            self.checkout(bug, working_dir)
            self.compile(bug)

            File_util.rm_dir(bug_pool_dir)
            shutil.copytree(working_dir, bug_pool_dir)

    def execute_and_save(self, bug, output_dir):
        if len(os.listdir(output_dir)) > 0:
            logger.info("pacth_diff is already done, just skip this step.")
            return

        patch_diff = self.get_patch_diff(bug)
        File_util.write_str_to_file(os.path.join(output_dir, "patch.diff"), patch_diff, False)
        File_util.write_list_to_file(os.path.join(output_dir, "failed_tests.txt"), self.failing_tests(bug))
        buggyloc_dict = self.get_buggy_locs(bug, patch_diff)
        Yaml_util.write_to_yaml(os.path.join(output_dir, "buggylocs.txt"), buggyloc_dict)
        pass

    def to_absolute(self, root, folders):
        absolute_folders = []
        for folder in folders:
            if os.path.exists(os.path.join(root, folder)):
                absolute_folders.append(os.path.join(root, folder))
        return absolute_folders

    def get_next_args(self, bug, args, output_dir):
        next_args = {}

        # seemingly ajra needs abs path (according to repairthemall)
        bin_folders = self.to_absolute(bug.get_working_dir(), self.bin_folders(bug))
        test_bin_folders = self.to_absolute(bug.get_working_dir(), self.test_bin_folders(bug))
        sources = self.to_absolute(bug.get_working_dir(), self.source_folders(bug))

        next_args["srcJavaDir"] = ":".join(sources)
        next_args["binJavaDir"] = ":".join(bin_folders)
        next_args["binTestDir"] = ":".join(test_bin_folders)
        next_args["dependencies"] = self.classpath(bug)
        next_args["jvmPath"] = self.get_jvm_path(bug)
        next_args["failedTests"] = ":".join(self.failing_tests(bug))
        next_args["failedTestMethods"] = ":".join(self.failing_test_methods(bug))
        next_args["workingDir"] = bug.get_working_dir()

        # extra args
        output_dir = os.path.join(output_dir, "..", args.fault_localizer.lower())
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        next_args['outputDir'] = output_dir

        return next_args

    @abc.abstractclassmethod
    def checkout(self, bug, output_dir):
        pass

    @abc.abstractclassmethod
    def failing_test_methods(self, bug):
        pass

    @abc.abstractclassmethod
    def compile(self, bug):
        pass

    @abc.abstractclassmethod
    def source_folders(self, bug):
        pass

    @abc.abstractclassmethod
    def bin_folders(self, bug):
        pass

    @abc.abstractclassmethod
    def test_bin_folders(self, bug):
        pass

    @abc.abstractclassmethod
    def classpath(self, bug):
        pass

    @abc.abstractclassmethod
    def failing_tests(self, bug):
        pass

    def set_bug_working_dir(self, bug, output_dir):
        assert output_dir is not None
        output_dir = os.path.join(output_dir, bug.name)
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        # if os.path.exists(output_dir):
        #     File_util.rm_dir(output_dir)
        bug.set_working_dir(output_dir)

    def _get_project_info(self, bug):
        try:
            return bug.maven_info
        except AttributeError:
            pass
        cmd = """cd %s;
mvn com.github.tdurieux:project-config-maven-plugin:1.0-SNAPSHOT:info -q;
""" % (bug.working_directory)
        info = json.loads(subprocess.check_output(cmd, shell=True))
        bug.maven_info = info
        return info

    @abc.abstractclassmethod
    def get_patch_diff(self, bug):
        pass

    @abc.abstractclassmethod
    def compliance_level(self, bug):
        pass

    def get_jvm_path(self, bug):
        java_version = Config.JAVA7_HOME
        if self.compliance_level(bug) > 7:
            java_version = Config.JAVA8_HOME
        return java_version

    def get_buggy_locs(self, bug, patch_diff):
        # patch = PatchSet.from_filename('tests/samples/bzr.diff', encoding='utf-8')
        patch_set = PatchSet.from_string(patch_diff)

        patch_dict = OrderedDict()

        for pacthed_file in patch_set:
            file_name = pacthed_file.source_file
            assert file_name.endswith(".java")
            assert "/" in file_name
            class_name = file_name.rsplit("/", 1)[1][:-len(".java")]

            hunks_list = []
            for hunk in pacthed_file:
                hunk_dict = OrderedDict()

                added_nos = []
                removed_nos = []
                for index in range(len(hunk)):
                    line = hunk[index]

                    # exclude cases: empty line
                    if line.value.strip() == "":
                        is_empty = False
                        if index == 0:
                            if hunk[index + 1].is_context:
                                is_empty = True
                        elif index == len(hunk) - 1:
                            if hunk[index - 1].is_context:
                                is_empty = True
                        else:
                            if hunk[index + 1].is_context and hunk[index - 1].is_context:
                                is_empty = True
                        if is_empty:
                            logger.debug(f"[empty line] remove this line: {line}")
                            continue

                    # exclude cases: quixbugs first line:package java_programs;
                    if bug.dataset.name == "quixbugs":
                        if line.value.strip() == "package java_programs;":
                            continue

                    # check current line
                    if line.is_removed:
                        removed_nos.append(line.source_line_no)
                        continue
                    if line.is_added:
                        # prev lines
                        if index > 0:
                            prev = hunk[index - 1]
                            if prev.is_removed:
                                continue
                            if prev.is_added:
                                # check if there is any neighbor removed line
                                continue_flag = False
                                if index - 1 > 0:
                                    for prev_ind in range(index - 2, -1, -1):
                                        prev_prev = hunk[prev_ind]
                                        if prev_prev.is_removed:
                                            continue_flag = True
                                            break
                                        if prev_prev.is_context:
                                            continue_flag = False
                                            break
                                if continue_flag:
                                    continue

                            if prev.is_context:
                                added_nos.append(prev.source_line_no)

                        # next lines
                        if index < len(hunk) - 1:
                            next = hunk[index + 1]
                            if next.is_removed:
                                added_nos.clear()
                                continue
                            if next.is_context:
                                added_nos.append(next.source_line_no)
                    # todo: consider cases where the added line is the last line of the src_file
                hunk_dict["removed"] = removed_nos
                hunk_dict["added"] = added_nos

                if len(removed_nos) + len(added_nos) != 0:
                    hunks_list.append(hunk_dict)
            patch_dict[class_name] = hunks_list
        return patch_dict