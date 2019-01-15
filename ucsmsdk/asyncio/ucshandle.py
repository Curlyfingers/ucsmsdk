# Copyright 2015 Cisco Systems, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import re
import logging

from .. import ucsgenutils
from .. import ucscoreutils
from ..ucsexception import UcsException
from ..ucsconstants import NamingId
from ..ucsmethodfactory import config_resolve_classes
from .ucssession import UcsSession

log = logging.getLogger('ucs')


class UcsHandle(UcsSession):
    """
    UcsHandle class is the user interface point for any Ucs related communication.

    Args:
        ip (str): The IP or Hostname of the UCS Server
        username (str): The username as configured on the UCS Server
        password (str): The password as configured on the UCS Server
        port (int or None): The port number to be used during connection
        secure (bool or None): True for secure connection, otherwise False
        proxy (str): The proxy object to be used to connect

    Example:
        handle = UcsHandle("192.168.1.1","admin","password")\n
        handle = UcsHandle("192.168.1.1","admin","password", secure=True)\n
        handle = UcsHandle("192.168.1.1","admin","password", secure=False)\n
        handle = UcsHandle("192.168.1.1","admin","password", port=80)\n
        handle = UcsHandle("192.168.1.1","admin","password", port=443)\n
        handle = UcsHandle("192.168.1.1","admin","password", port=100, secure=True)\n
        handle = UcsHandle("192.168.1.1","admin","password", port=100, secure=False)\n
    """

    def __init__(self, ip, username, password, port=None, secure=None,
                 proxy=None, timeout=None):
        UcsSession.__init__(self, ip, username, password, port, secure, proxy,
                timeout)
        self.__commit_buf = {}
        self.__commit_buf_tagged = {}

    def set_dump_xml(self):
        """
        Enables the logging of xml requests and responses.
        """

        self._set_dump_xml()

    def unset_dump_xml(self):
        """
        Disables the logging of xml requests and responses.
        """

        self._unset_dump_xml()

    def set_timeout(self, timeout=None):
        """
        Sets timeout (in seconds) for the request
        """

        self._set_timeout(timeout)

    def unset_timeout(self):
        """
        Disable timeout for the request
        """

        self._unset_timeout()

    async def login(self, auto_refresh=False, force=False, timeout=None):
        """
        Initiates a connection to the server referenced by the UcsHandle.
        A cookie is populated in the UcsHandle, if the login is successful.

        Args:
            auto_refresh (bool): if set to True, it refresh the cookie
                continuously
            force (bool): if set to True it reconnects even if cookie exists
                and is valid for respective connection.
            timeout (int): timeout value in secs

        Returns:
            True on successful connect

        Example:
            await handle.login()\n
            await handle.login(auto_refresh=True)\n
            await handle.login(force=True)\n
            await handle.login(auto_refresh=True, force=True)\n

            where handle is UcsHandle()
        """

        return await self._login(auto_refresh, force, timeout=timeout)

    async def logout(self, timeout=None):
        """
        Disconnects from the server referenced by the UcsHandle.

        Args:
            timeout (int): timeout value in secs

        Returns:
            True on successful disconnect

        Example:
            await handle.logout()

            where handle is UcsHandle()
        """

        return await self._logout()

    async def process_xml_elem(self, elem, timeout=None):
        """
        process_xml_elem is a helper method which posts xml elements to the
        server and returns parsed response. It's role is to operate on the
        output of methods from ucsmethodfactory, which return xml element
        node(s).

        Args:
            elem (xml element object)
            timeout (int): timeout value in secs

        Returns:
            mo list or external method object

        Example:
            elem = ucsmethodfactory.config_find_dns_by_class_id(cookie=handle.cookie, class_id="LsServer", in_filter=None)\n
            dn_objs = await handle.process_xml_elem(elem)
        """

        response = await self.post_elem(elem, timeout=timeout)
        if response.error_code != 0:
            raise UcsException(response.error_code, response.error_descr)

        if hasattr(response, "out_config"):
            return response.out_config.child
        elif hasattr(response, "out_configs"):
            mo_list = []
            pair_flag = False
            for ch_ in response.out_configs.child:
                if pair_flag or ch_.get_class_id() != "Pair":
                    mo_list.append(ch_)
                elif ch_.get_class_id() == "Pair":
                    mo_list.extend(ch_.child)
                    pair_flag = True
            return mo_list
        elif hasattr(response, "out_dns"):
            return response.out_dns.child
        else:
            return response

    async def get_auth_token(self, timeout=None):
        """
        Returns a token that is used for UCS authentication.

        Args:
            None

        Returns:
            auth_token (str)
            timeout (int): timeout value in secs

        Example:
            await handle.get_auth_token()

        """

        from ..ucsmethodfactory import aaa_get_n_compute_auth_token_by_dn

        auth_token = None
        mo = await self.query_classid(class_id=NamingId.COMPUTE_BLADE)
        if not mo:
            mo = await self.query_classid(class_id=NamingId.COMPUTE_RACK_UNIT)

        if mo:
            elem = aaa_get_n_compute_auth_token_by_dn(
                cookie=self.cookie,
                in_cookie=self.cookie,
                in_dn=mo[0].dn,
                in_number_of=1)
            response = await self.post_elem(elem, timeout=timeout)
            if response.error_code != 0:
                raise UcsException(response.error_code,
                                   response.error_descr)

            # cat = self.AaaGetNComputeAuthTokenByDn(mo[0].Dn, 1, None)
            auth_token = response.out_tokens.split(',')[0]

        return auth_token

    async def query_dns(self, *dns):
        """
        Queries multiple obects from the server based of a comma separated list
        of their distinguised names.

        Args:
            dns (list or comma separated strings): distinguished names to be
                queried for

        Returns:
            Dictionary {dn1: object, dn2: object2}

        Example:
            obj = await handle.query_dns("fabric/lan/net-100", "fabric/lan/net-101")
            obj = await handle.query_dns(["fabric/lan/net-100", "fabric/lan/net-101"])
        """

        from ..ucsbasetype import DnSet, Dn
        from ..ucsmethodfactory import config_resolve_dns

        if not dns:
            raise ValueError("Provide a list or Comma Separated string of Dns")

        dn_list = []
        for dn in dns:
            if isinstance(dn, list):
                for each in dn:
                    dn_list.append(each.strip())
            elif isinstance(dn, str):
                dn_list.append(dn.strip())

        dn_dict = {}
        for dn_ in dn_list:
            dn_dict[dn_] = None

        dn_set = DnSet()
        for dn_ in dn_dict:
            dn_obj = Dn()
            dn_obj.value = dn_
            dn_set.child_add(dn_obj)

        elem = config_resolve_dns(cookie=self.cookie,
                                  in_dns=dn_set)
        response = await self.post_elem(elem)
        if response.error_code != 0:
            raise UcsException(response.error_code, response.error_descr)

        for out_mo in response.out_configs.child:
            dn_dict[out_mo.dn] = out_mo

        return dn_dict

    async def query_classids(self, *class_ids):
        """
        Queries multiple obects from the server based of a comma separated list
        of their class Ids.

        Args:
            class_ids (list or comma separated strings): Class Ids to be
                                                        queried for

        Returns:
        Dictionary {class_id1: [objects], class_id2: [objects]}

        Example:
            obj = await handle.query_classids("OrgOrg", "LsServer")
            obj = await handle.query_classids(["OrgOrg", "LsServer"])
        """

        # ToDo - How to handle unknown class_id
        from ..ucsbasetype import ClassIdSet, ClassId
        from ..ucsmeta import MO_CLASS_ID

        if not class_ids:
            raise ValueError("Provide a list or Comma Separated string of \
                             Class Ids")

        class_id_list = []
        for class_id in class_ids:
            if isinstance(class_id, list):
                for each in class_id:
                    class_id_list.append(each.strip())
            elif isinstance(class_id, str):
                class_id_list.append(class_id.strip())

        class_id_dict = {}
        class_id_set = ClassIdSet()

        for class_id_ in class_id_list:
            class_id_obj = ClassId()

            meta_class_id = ucsgenutils.word_u(class_id_)
            if meta_class_id in MO_CLASS_ID:
                class_id_dict[meta_class_id] = []
                class_id_obj.value = ucsgenutils.word_l(class_id_)
            else:
                class_id_dict[class_id_] = []
                class_id_obj.value = class_id_

            class_id_set.child_add(class_id_obj)

        elem = config_resolve_classes(cookie=self.cookie,
                                      in_ids=class_id_set)
        response = await self.post_elem(elem)
        if response.error_code != 0:
            raise UcsException(response.error_code, response.error_descr)

        for out_mo in response.out_configs.child:
            class_id_dict[out_mo._class_id].append(out_mo)

        return class_id_dict

    async def query_dn(self, dn, hierarchy=False, need_response=False, timeout=None):
        """
        Finds an object using it's distinguished name.

        Args:
            dn (str): distinguished name of the object to be queried for.
            hierarchy(bool): True/False,
                                get all objects in hierarchy if True
            need_response(bool): True/False,
                                return the response xml node, instead of parsed
                                objects
            timeout (int): timeout value in secs

        Returns:
            managedobject or None   by default\n
            managedobject list      if hierarchy=True\n
            externalmethod object   if need_response=True\n

        Example:
            obj = await handle.query_dn("fabric/lan/net-100")\n
            obj = await handle.query_dn("fabric/lan/net-100", hierarchy=True)\n
            obj = await handle.query_dn("fabric/lan/net-100", need_response=True)\n
            obj = await handle.query_dn("fabric/lan/net-100", hierarchy=True, need_response=True)\n
        """

        from ..ucsbasetype import DnSet, Dn
        from ..ucsmethodfactory import config_resolve_dns

        if not dn:
            raise ValueError("Provide dn.")

        dn_set = DnSet()
        dn_obj = Dn()
        dn_obj.value = dn
        dn_set.child_add(dn_obj)

        elem = config_resolve_dns(cookie=self.cookie,
                                  in_dns=dn_set,
                                  in_hierarchical=hierarchy)
        response = await self.post_elem(elem, timeout=timeout)
        if response.error_code != 0:
            raise UcsException(response.error_code, response.error_descr)

        if need_response:
            return response

        if hierarchy:
            out_mo_list = ucscoreutils.extract_molist_from_method_response(
                response,
                hierarchy)
            return out_mo_list

        mo = None
        if len(response.out_configs.child) > 0:
            mo = response.out_configs.child[0]
        return mo

    async def query_classid(self, class_id=None, filter_str=None, hierarchy=False,
                      need_response=False, timeout=None):
        """
        Finds an object using it's class id.

        Args:
            class_id (str): class id of the object to be queried for.
            filter_str(str): query objects with specific property with specific value or pattern specifying value.

                      (property_name, "property_value, type="filter_type")\n
                      property_name: Name of the Property\n
                      property_value: Value of the property (str or regular expression)\n
                      filter_type: eq - equal to\n
                                   ne - not equal to\n
                                   ge - greater than or equal to\n
                                   gt - greater than\n
                                   le - less than or equal to\n
                                   lt - less than\n
                                   re - regular expression\n

                      logical filter type: not, and, or\n

                      e.g. '(dn,"org-root/ls-C1_B1", type="eq") or (name, "event", type="re", flag="I")'\n
            hierarchy(bool): if set to True will return all the child
                             hierarchical objects.
            need_response(bool): if set to True will return only response
                                object.
            timeout (int): timeout value in secs


        Returns:
            managedobjectlist or None   by default\n
            managedobjectlist or None   if hierarchy=True\n
            methodresponse              if need_response=True\n

        Example:
            obj = await handle.query_classid(class_id="LsServer")\n
            obj = await handle.query_classid(class_id="LsServer", hierarchy=True)\n
            obj = await handle.query_classid(class_id="LsServer", need_response=True)\n

            filter_str = '(dn,"org-root/ls-C1_B1", type="eq") or (name, "event", type="re", flag="I")'\n
            obj = await handle.query_classid(class_id="LsServer", filter_str=filter_str)\n
        """

        # ToDo - How to handle unknown class_id

        from ..ucsfilter import generate_infilter
        from ..ucsmethodfactory import config_resolve_class

        if not class_id:
            raise ValueError("Provide Parameter class_id")

        meta_class_id = ucscoreutils.find_class_id_in_mo_meta_ignore_case(
            class_id)
        if meta_class_id:
            is_meta_class_id = True
        else:
            meta_class_id = class_id
            is_meta_class_id = False

        if filter_str:
            in_filter = generate_infilter(meta_class_id, filter_str,
                                          is_meta_class_id)
        else:
            in_filter = None

        elem = config_resolve_class(cookie=self.cookie,
                                    class_id=meta_class_id,
                                    in_filter=in_filter,
                                    in_hierarchical=hierarchy)
        response = await self.post_elem(elem, timeout=timeout)
        if response.error_code != 0:
            raise UcsException(response.error_code, response.error_descr)

        if need_response:
            return response

        out_mo_list = ucscoreutils.extract_molist_from_method_response(
            response,
            hierarchy)
        return out_mo_list

    async def query_children(self, in_mo=None, in_dn=None, class_id=None,
                       filter_str=None, hierarchy=False, timeout=None):
        """
        Finds children of a given managed object or distinguished name.
        Arguments can be specified to query only a specific type(class_id)
        of children.
        Arguments can also be specified to query only direct children or the
        entire hierarchy of children.

        Args:
            in_mo (managed object): query children managed object under this
                                        object.
            in_dn (dn string): query children managed object for a
                                given managed object of the respective dn.
            class_id(str): by default None, if given find only specific
                            children object for a given class_id.
            filter_str(str): query objects with specific property with specific
                            value or pattern specifying value.

                      (property_name, "property_value, type="filter_type")\n
                      property_name: Name of the Property\n
                      property_value: Value of the property (str or regular
                                      expression)\n
                      filter_type: eq - equal to\n
                                   ne - not equal to\n
                                   ge - greater than or equal to\n
                                   gt - greater than\n
                                   le - less than or equal to\n
                                   lt - less than\n
                                   re - regular expression\n

                      logical filter type: not, and, or\n

                      e.g. '(dn,"org-root/ls-C1_B1", type="eq") or (name,
                                            "event", type="re", flag="I")'\n
            hierarchy(bool): if set to True will return all the child
                             hierarchical objects.
            need_response(bool): if set to True will return only response
                                object.
            hierarchy(bool): if set to True will return all the child
                              hierarchical objects.
            timeout (int): timeout value in secs

        Returns:
            managedobjectlist or None   by default\n
            managedobjectlist or None   if hierarchy=True\n

        Example:
            mo_list = await handle.query_children(in_mo=mo)\n
            mo_list = await handle.query_children(in_mo=mo, class_id="classid")\n
            mo_list = await handle.query_children(in_dn=dn)\n
            mo_list = await handle.query_children(in_dn=dn, class_id="classid")\n
            mo_list = await handle.query_children(in_dn="org-root",
                        class_id="LsServer", filter_str="(usr_lbl, 'test')")
            mo_list = await handle.query_children(in_dn="org-root",
                class_id="LsServer", filter_str="(usr_lbl, 'test', type='eq')")
        """

        from ..ucsfilter import generate_infilter
        from ..ucsmethodfactory import config_resolve_children

        if not in_mo and not in_dn:
            raise ValueError('[Error]: GetChild: Provide in_mo or in_dn.')

        if in_mo:
            parent_dn = in_mo.dn
        elif in_dn:
            parent_dn = in_dn

        in_filter = None

        if class_id:
            meta_class_id = ucscoreutils.find_class_id_in_mo_meta_ignore_case(
                class_id)
            if meta_class_id:
                is_meta_class_id = True
            else:
                meta_class_id = class_id
                is_meta_class_id = False

            if filter_str:
                in_filter = generate_infilter(meta_class_id, filter_str,
                                              is_meta_class_id)
        else:
            meta_class_id = class_id

        elem = config_resolve_children(cookie=self.cookie,
                                       class_id=meta_class_id,
                                       in_dn=parent_dn,
                                       in_filter=in_filter,
                                       in_hierarchical=hierarchy)
        response = await self.post_elem(elem, timeout=timeout)
        if response.error_code != 0:
            raise UcsException(response.error_code, response.error_descr)

        out_mo_list = ucscoreutils.extract_molist_from_method_response(
            response,
            hierarchy)

        return out_mo_list

    def _get_commit_buf(self, tag=None):
        if tag is None:
            return self.__commit_buf

        if tag not in self.__commit_buf_tagged:
            self.__commit_buf_tagged[tag] = {}

        return self.__commit_buf_tagged[tag]

    def _update_commit_buf(self, mo, tag=None):
        if tag is None:
            self.__commit_buf[mo.dn] = mo
            return

        if tag not in self.__commit_buf_tagged:
            self.__commit_buf_tagged[tag] = {}

        self.__commit_buf_tagged[tag][mo.dn] = mo

    def add_mo(self, mo, modify_present=False, tag=None):
        """
        Adds a managed object to the UcsHandle commit buffer.
        This method does not trigger a commit by itself.
        This needs to be followed by a handle.commit() either immediately or
        after more operations to ensure successful addition of object on server

        Args:
            mo (managedobject): ManagedObject to be added.
            modify_present (bool): True/False,
                                    overwrite existing object if True

        Returns:
            None

        Example:
            obj = handle.add_mo(mo)\n
            await handle.commit()\n
        """

        if modify_present in ucsgenutils.AFFIRMATIVE_LIST:
            mo.status = "created,modified"
        else:
            mo.status = "created"

        self._update_commit_buf(mo, tag)

    def set_mo(self, mo, tag=None):
        """
        Modifies a managed object and adds it to UcsHandle commit buffer (if
         not already in it).
        This method does not trigger a commit by itself.
        This needs to be followed by a handle.commit() either immediately or
        after more operations to ensure successful modification of object on
        server.

        Args:
            mo (managedobject): Managed object with modified properties.

        Returns:
            None

        Example:
            obj = handle.set_mo(mo)\n
            await handle.commit()\n
        """

        mo.status = "modified"
        self._update_commit_buf(mo, tag)

    def remove_mo(self, mo, tag=None):
        """
        Removes a managed object.
        This method does not trigger a commit by itself.
        This needs to be followed by a handle.commit() either immediately or
        after more operations to ensure successful removal of object from the
        server.

        Args:
            mo (managedobject): Managed object to be removed.

        Returns:
            None

        Example:
            obj = handle.remove_mo(mo)\n
            await handle.commit()\n
        """

        mo.status = "deleted"
        if mo.parent_mo:
            mo.parent_mo.child_remove(mo)

        self._update_commit_buf(mo, tag)

    async def estimate_impact(self, tag=None, timeout=None):
        from ..ucsbasetype import ConfigMap, Pair
        from ..ucsmethodfactory import config_estimate_impact

        tag = self._auto_set_tag_context(tag)
        mo_dict = self._get_commit_buf(tag)
        if not mo_dict:
            return None
        config_map = ConfigMap()
        for mo_dn in mo_dict:
            mo = mo_dict[mo_dn]
            child_list = mo.child
            while len(child_list) > 0:
                current_child_list = child_list
                child_list = []
                for child_mo in current_child_list:
                    child_list.extend(child_mo.child)

            pair = Pair()
            pair.key = mo_dn
            pair.child_add(mo_dict[mo_dn])
            config_map.child_add(pair)

        elem = config_estimate_impact(self.cookie, config_map)
        response = await self.post_elem(elem, timeout=timeout)
        if response.error_code != 0:
            raise UcsException(response.error_code, response.error_descr)

        return self._get_transaction_impact(response)

    def _get_impact_message(self, l_type, params):
        msg = []
        msg_prefix = {
            "warning": "Will cause a non fatal configuration warning for",
            "failure": "Will cause a configuration failure of",
            "reboot_immediate": "Will cause the immediate reboot of",
            "reboot_user_ack": "Will require user acknowledgement before the reboot of",
            "reboot_timer": "Will reboot in the maintenance interval of"
        }
        msg.append(msg_prefix[l_type] + ":")
        msg.append(params["class_id"] + " " + params["name"] + "(" + params["dn"] + ")")
        if params["pn_dn"]:
            msg.append("[Server: " + params["pn_dn"] + "]")
        if params["issues"]:
            msg.append("Reason: " + params["issues"])
        return ' '.join(msg)

    def _get_transaction_impact(self, rsp):
        """
        Outputs a json object, that can have the following keys,
            "failure", "warning", "reboot_user_ack", "reboot_immediate",
            "reboot_maintenance", "misc"
        """
        from ..mometa.lsmaint.LsmaintAck import LsmaintAck, LsmaintAckConsts
        from ..mometa.equipment.EquipmentChassis import EquipmentChassis
        from ..ucsmo import ManagedObject
        import json

        ackables = []
        affected = []
        old_ackables = []
        old_affected = []
        msg_buf = {}

        if not rsp:
            return json.dumps(msg_buf)

        for pair in rsp.out_ackables.child:
            for mo in pair.child:
                if not isinstance(mo, ManagedObject):
                    continue
                ackables.append(mo)

        for pair in rsp.out_affected.child:
            for mo in pair.child:
                if not isinstance(mo, ManagedObject):
                    continue
                affected.append(mo)

        for pair in rsp.out_old_ackables.child:
            for mo in pair.child:
                if not isinstance(mo, ManagedObject):
                    continue
                old_ackables.append(mo)

        for pair in rsp.out_old_affected.child:
            for mo in pair.child:
                if not isinstance(mo, ManagedObject):
                    continue
                old_affected.append(mo)

        if len(ackables) == 0:
            for mo in affected:
                if not isinstance(mo, EquipmentChassis):
                    continue
                if 'chassis-vif-capacity-reduced' in mo.oper_qualifier:
                    msg_buf["misc"] = """Detected reduced VIF capacity. Check if all fabric port channel member links are connected to the same port group on fabric interconnect. If not then change the connectivity accordingly and re-acknowledge the chassis."""
                elif 'chassis-port-channel-enabled' in mo.oper_qualifier:
                    msg_buf["misc"] = """Connectivity mode changed to fabric port channel on one or both the IOCards. This might result in VIF capacity decrease on IOCard."""
            return json.dumps(msg_buf)

        for ack in ackables:
            if isinstance(ack, LsmaintAck):
                m = re.search('(.*)/(.*)$', ack.dn)
                parent_dn = m.group(1)

                for each in old_ackables:
                    if ack.dn == each.dn:
                        pending_ack = ack
                        break

                if ack.config_issues:
                    for each in affected:
                        if each.dn == parent_dn:
                            server_mo = each
                            break

                    for each in old_affected:
                        if each.dn == parent_dn:
                            orig_server_mo = each
                            break

                    if server_mo and server_mo.status != "deleted":
                        if (server_mo.config_state == "applied" and ack.config_issues !=
                                "incompatible-bios-image" and ack.config_issues != "invalid-wwn"):
                            l_type = "warning"
                        else:
                            l_type = "failure"

                        params = {
                            "class_id": server_mo._class_id,
                            "name": server_mo.name,
                            "dn": server_mo.dn,
                            "pn_dn": server_mo.pn_dn,
                            "issues": ack.config_issues
                        }

                        params["message"] = self._get_impact_message(l_type,
                                                                     params)

                        if l_type not in msg_buf:
                            msg_buf[l_type] = []

                        if orig_server_mo.config_qualifier:
                            params["pending"] = {
                                "conf_qual": orig_server_mo.config_qualifier,
                                "message": "Warning: Due to the presence of pre-existing configuration issues the impact of the current changes cannot be properly evaluated"
                            }
                        msg_buf[l_type].append(params)

                if ack.disr:
                    if ack.deployment_mode == LsmaintAckConsts.DEPLOYMENT_MODE_IMMEDIATE:
                        l_type = "reboot_immediate"
                    elif ack.deployment_mode == LsmaintAckConsts.DEPLOYMENT_MODE_TIMER_AUTOMATIC:
                        l_type = "reboot_timer"
                    elif ack.deployment_mode == LsmaintAckConsts.DEPLOYMENT_MODE_USER_ACK:
                        l_type = "reboot_user_ack"
                    else:
                        continue

                    for each in affected:
                        if each.dn == parent_dn:
                            server_mo = each
                            break

                    if server_mo:
                        params = {
                                "class_id": server_mo._class_id,
                                "name": server_mo.name,
                                "dn": server_mo.dn,
                                "pn_dn": server_mo.pn_dn,
                                "issues": ack.config_issues
                             }
                        params["message"] = self._get_impact_message(l_type,
                                                                     params)

                        if l_type not in msg_buf:
                            msg_buf[l_type] = []

                        if pending_ack and pending_ack.disr:
                            params["pending"] = {
                                "old_pn_dn": pending_ack.old_pn_dn,
                                "change_details": pending_ack.change_details,
                                "changed_by": pending_ack.change_by,
                                "message": "Pre-existing pending disruptions: " + pending_ack.change_details
                            }
                        msg_buf[l_type].append(params)
        return json.dumps(msg_buf, indent=4)

    async def commit(self, tag=None, timeout=None):
        """
        Commit the buffer to the server. Pushes all the configuration changes
        so far to the server.
        Configuration could be added to the commit buffer using add_mo(),
        set_mo(), remove_mo() prior to making a handle.commit()

        Args:
            None
            timeout (int): timeout value in secs

        Returns:
            None

        Example:
            await handle.commit()\n
        """

        from ..ucsbasetype import ConfigMap, Dn, DnSet, Pair
        from ..ucsmethodfactory import config_resolve_dns
        from ..ucsmethodfactory import config_conf_mos

        refresh_dict = {}
        mo_dict = self._get_commit_buf(tag)
        if not mo_dict:
            return None

        config_map = ConfigMap()
        for mo_dn in mo_dict:
            mo = mo_dict[mo_dn]
            child_list = mo.child
            while len(child_list) > 0:
                current_child_list = child_list
                child_list = []
                for child_mo in current_child_list:
                    if child_mo.is_dirty():
                        refresh_dict[child_mo.dn] = child_mo
                    child_list.extend(child_mo.child)

            pair = Pair()
            pair.key = mo_dn
            pair.child_add(mo_dict[mo_dn])
            config_map.child_add(pair)

        elem = config_conf_mos(self.cookie, config_map,
                               False)
        response = await self.post_elem(elem, timeout=timeout)
        if response.error_code != 0:
            self.commit_buffer_discard(tag)
            raise UcsException(response.error_code, response.error_descr)

        for pair_ in response.out_configs.child:
            for out_mo in pair_.child:
                out_mo.sync_mo(mo_dict[out_mo.dn])

        if refresh_dict:
            dn_set = DnSet()
            for dn_ in refresh_dict:
                dn_obj = Dn()
                dn_obj.value = dn_
                dn_set.child_add(dn_obj)

            elem = config_resolve_dns(cookie=self.cookie,
                                      in_dns=dn_set)
            response = await self.post_elem(elem, timeout=timeout)
            if response.error_code != 0:
                raise UcsException(response.error_code,
                                   response.error_descr)

            for out_mo in response.out_configs.child:
                out_mo.sync_mo(refresh_dict[out_mo.dn])

        self.commit_buffer_discard(tag)

    def commit_buffer_discard(self, tag=None):
        """
        Discard the configuration changes in the commit buffer.

        Args:
            None

        Returns:
            None

        Example:
            handle.commit_buffer_discard()
        """

        if tag is None:
            self.__commit_buf = {}

        if tag in self.__commit_buf_tagged:
            del self.__commit_buf_tagged[tag]

    async def is_valid(self):
        """
        Checks if the cookie in the handle is still valid
        """

        # If the cookie is not valid, we will receive an exception.
        try:
            await self.query_dn("org-root")
        except Exception as e:
            return False
        return True

    def freeze(self):
        return self._freeze()

    @staticmethod
    async def unfreeze(frozen_handle):
        handle = UcsHandle("", "", "")
        await handle._unfreeze(frozen_handle)
        return handle
