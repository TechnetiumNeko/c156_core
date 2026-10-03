"""Concrete inheritance, visibility and freeze behavior over pure values."""
import unittest
from types import SimpleNamespace as Value

from src.access import AccessPolicy, AccessRule
from src.core.errors import Forbidden, Frozen, NotFound
from src.identity.models import Principal

ROOT = Value(object_id='root', kind='folder')
FOLDER = Value(object_id='internal', kind='folder')
DOC = Value(object_id='bio', kind='document')
CHAIN = (ROOT, FOLDER, DOC)


def rule(node, subject, key, action, effect):
    return AccessRule(node, subject, key, action, effect)


def policy(role='editor', user='alice', *, rules=(), read_scope='members',
           privacy=None, locks=None, site_admin=False, status='active'):
    membership = Value(user_id=user, role=role, status=status) if role else None
    return AccessPolicy(Principal(user, site_admin), membership,
                        Value(version=3, read_scope=read_scope), rules=rules,
                        privacy=privacy or {}, locks=locks or {}, ownership={})


class AccessPolicyTests(unittest.TestCase):
    def test_unreadable_parent_closes_child_allow(self):
        p = policy('reader', rules=(rule('internal', 'role', 'reader', 'read', 'deny'),
                                   rule('bio', 'role', 'reader', 'read', 'allow')))
        self.assertFalse(p.can_read(CHAIN))
        self.assertEqual(p.decide(CHAIN, 'read').source_object_id, 'internal')
        with self.assertRaises(NotFound):
            p.require_action(CHAIN, 'read')

    def test_readable_parent_edit_deny_allows_child_exception(self):
        p = policy(rules=(rule('internal', 'role', 'editor', 'edit', 'deny'),
                          rule('bio', 'user', 'alice', 'edit', 'allow')))
        self.assertTrue(p.decide(CHAIN, 'edit').allowed)
        self.assertEqual(p.decide(CHAIN, 'edit').source_object_id, 'bio')

    def test_nearest_layer_then_user_priority_not_union(self):
        p = policy(rules=(rule('root', 'user', 'alice', 'edit', 'allow'),
                          rule('bio', 'role', 'editor', 'edit', 'deny')))
        self.assertFalse(p.decide(CHAIN, 'edit').allowed)
        p = policy(rules=p.rules + (rule('bio', 'user', 'alice', 'edit', 'allow'),))
        self.assertTrue(p.decide(CHAIN, 'edit').allowed)
        p = policy(role=None, rules=(rule('bio', 'authenticated', '', 'read', 'deny'),
                                    rule('bio', 'everyone', '', 'read', 'allow')),
                   read_scope='everyone')
        self.assertFalse(p.can_read(CHAIN))

    def test_public_read_and_active_member_write_gate(self):
        self.assertFalse(policy(role=None, user=None).can_read(CHAIN))
        self.assertTrue(policy(role=None, read_scope='authenticated').can_read(CHAIN))
        self.assertFalse(policy(role=None, user=None, read_scope='authenticated').can_read(CHAIN))
        self.assertTrue(policy(role=None, user=None, read_scope='everyone').can_read(CHAIN))
        p = policy(role=None, read_scope='everyone',
                   rules=(rule('bio', 'user', 'alice', 'edit', 'allow'),))
        with self.assertRaises(Forbidden):
            p.require_action(CHAIN, 'edit')
        self.assertFalse(policy(status='removed', read_scope='everyone').decide(CHAIN, 'edit').allowed)
        self.assertFalse(policy().decide(CHAIN, 'review').allowed)
        self.assertFalse(policy().decide(CHAIN, 'publish').allowed)
        self.assertFalse(policy('owner').decide(CHAIN, 'create').allowed)
        self.assertTrue(policy().decide(CHAIN[:-1], 'create').allowed)

    def test_private_owner_still_needs_normal_rights_and_ancestry(self):
        private = {'internal': 'alice'}
        self.assertFalse(policy(user='bob', privacy=private,
                                rules=(rule('bio', 'user', 'bob', 'read', 'allow'),)).can_read(CHAIN))
        self.assertTrue(policy(privacy=private).can_read(CHAIN))
        self.assertFalse(policy(role=None, privacy=private, read_scope='everyone').can_read(CHAIN))
        p = policy(privacy=private, rules=(rule('root', 'user', 'alice', 'read', 'deny'),))
        self.assertFalse(p.can_read(CHAIN))
        self.assertFalse(policy(privacy=private).decide(CHAIN, 'review').allowed)

    def test_workspace_admin_bypass_is_distinct_from_site_admin(self):
        rules = (rule('root', 'everyone', '', 'read', 'deny'),)
        self.assertFalse(policy(role=None, site_admin=True, rules=rules).can_read(CHAIN))
        self.assertTrue(policy('admin', rules=rules, privacy={'internal': 'bob'}).can_read(CHAIN))

    def test_locks_do_not_grant_rights_or_admin_bypass(self):
        p = policy(locks={'bio': 'alice'})
        p.require_action(CHAIN, 'edit')
        p.require_unfrozen((DOC,), operation='edit')
        p = policy('admin', locks={'bio': 'bob'})
        p.require_action(CHAIN, 'delete')
        with self.assertRaises(Frozen):
            p.require_unfrozen(CHAIN, operation='delete')
        p = policy('reader', locks={'bio': 'alice'})
        with self.assertRaises(Forbidden):
            p.require_action(CHAIN, 'edit')

    def test_input_collections_and_records_are_detached(self):
        rules = [rule('bio', 'user', 'alice', 'edit', 'deny')]
        privacy = {'bio': 'alice'}
        membership = Value(user_id='alice', role='editor', status='active')
        settings = Value(version=1, read_scope='members')
        p = AccessPolicy(Principal('alice', False), membership, settings,
                         rules=rules, privacy=privacy, locks={}, ownership={})
        rules.clear()
        privacy['bio'] = 'bob'
        membership.role = 'admin'
        settings.read_scope = 'everyone'
        self.assertTrue(p.can_read(CHAIN))
        self.assertFalse(p.decide(CHAIN, 'edit').allowed)


if __name__ == '__main__':
    unittest.main()
