__author__ = "dale mcdiarmid"


def next_vmid(vm_list, min_vmid=100):
    """
    Find the first available VMID number from a sorted list of VMIDs.

    Args:
        vmid_list (list): A list of vms representing used VMIDs
        min_vmid (int): The lowest VMID number to start from

    Returns:
        int: The first available VMID number
    """
    vmid_list = [int(vm["vmid"]) for vm in vm_list]
    sorted_vmids = sorted(vmid_list)
    if not sorted_vmids:
        return min_vmid

    if sorted_vmids[0] > min_vmid:
        return min_vmid

    for i in range(len(sorted_vmids) - 1):
        if sorted_vmids[i + 1] - sorted_vmids[i] > 1:
            return sorted_vmids[i] + 1

    return sorted_vmids[-1] + 1


class FilterModule:
    def filters(self):
        return {"next_vmid": next_vmid}
