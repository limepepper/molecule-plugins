# ruff: noqa: UP032, SLF001

import datetime
import os
import syslog
import time
from collections import defaultdict


class AnsibleAsyncExecutor:
    """
    This class handles taking a list of module invocations and executes
    them until they have resolved.
    """

    @staticmethod
    def async_executor(
        action_module,
        start,
        job_list,
        task_vars=None,
        common_args=None,
        logger=None,
    ):
        if not logger:

            def logger(*_, **__):
                pass

        job_results = defaultdict(dict)

        jobs_start = datetime.datetime.now()

        jobs = {}
        for key, job in job_list.items():
            # Add async parameters to module args
            async_args = job["mod_args"].copy()
            if common_args:
                async_args.update(common_args)
            if "description" in job:
                logger(
                    "Running queue: '{}' elapsed({})".format(
                        job["description"],
                        round((start - jobs_start).total_seconds(), 2),
                    ),
                )
            jobs[key] = action_module._execute_module(
                module_name=job["module_name"],
                module_args=async_args,
                task_vars=job.get("task_vars", task_vars),
                wrap_async=True,
            )

        while jobs:

            for key in jobs:
                poll_args = {
                    "jid": jobs[key]["ansible_job_id"],
                    "_async_dir": os.path.dirname(jobs[key]["results_file"]),
                }
                res = action_module._execute_module(
                    module_name="ansible.legacy.async_status",
                    module_args=poll_args,
                    task_vars=task_vars,
                    wrap_async=False,
                )
                syslog.syslog(f"res {res}")
                if res.get("finished", 0) == 1:
                    if res.get("failed", False):
                        job_results[key]["state"] = "failed"
                    elif res.get("skipped", False):
                        job_results[key]["state"] = "skipped"
                    else:
                        job_results[key]["state"] = "finished"
                    job_results[key]["key"] = key
                    job_results[key]["elapsed"] = round(
                        (datetime.datetime.now() - jobs_start).total_seconds(),
                        2,
                    )
                    job_results[key]["module_name"] = job_list[key]["module_name"]
                    job_results[key]["data"] = res
                    job_results[key]["description"] = job_list[key].get(
                        "description",
                        "",
                    )
                    job_results[key]["mod_args"] = job_list[key].get("mod_args", {})
                    del jobs[key]
                    break
                else:
                    time.sleep(0.2)
            else:
                time.sleep(0.8)

        return job_results
