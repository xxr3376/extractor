#! encoding=utf-8
from lxml import etree
import xml.etree.ElementTree as ET
import re
import json
import urllib
import logging
import lxml.html
from lxml.cssselect import CSSSelector

function_re = re.compile(r'([a-z_0-9]+)(?:\(([\sa-zA-Z0-9,\-_]+)\))?')

find_id_re = re.compile(r'^.*?(\d+).*?$')

class NoAttributeException(Exception):
  pass

def attr_getter(ctx):
  ret = ctx.dom.get(ctx.arg0)
  if not ret:
    raise NoAttributeException()
  return ret
functions = {
  'attr': attr_getter,
  'int': lambda ctx: int(ctx.pipe, 10),
  'text': lambda ctx: ctx.dom.text_content(),
  'regex': lambda ctx: ctx.regex(ctx.arg0),
  'default': lambda ctx: ctx.pipe if ctx.pipe else ctx.arg0 if ctx.arg_len > 0 else "",
  'unquote': lambda ctx: urllib.unquote(ctx.pipe),
  'decode': lambda ctx: ctx.pipe.decode(ctx.arg0),
  'strip': lambda ctx: ctx.pipe.strip(),
  'exist': lambda ctx: not(ctx.dom == None),
  'html': lambda ctx: etree.tostring(ctx.dom),

}

class Context():
  def __init__(self, extractor, dom, args = "", pipe = None):
    self.extractor = extractor
    self.dom = dom
    self.pipe = pipe
    self.set_args(args)

  def set_args(self, args):
    if args:
      self.args = map(lambda x: x.strip(), args.split(','))
    else:
      self.args = []

  def arg(self, idx):
    return self.args[idx]

  @property
  def arg0(self):
    return self.arg(0)
  @property
  def arg_len(self):
    return len(self.args)

  def regex(self, name):
    ele = self.extractor.regex_list[name]
    try:
      return ele['re'].match(self.pipe).group(ele['group'])
    except:
      return None

class Extractor():
  def __init__(self, f):
    self.tree = ET.parse(f).getroot()
    self.regex_list = {}
    self.debug_counter = 0
    self.debug_mode = False

    for child in self.tree:
      if child.tag == 'define':
        for define in child:
          self.init_define(define)
      elif child.tag == 'page':
        self.page = child

  def init_define(self, define):
    if define.tag == 'regex':
      name = define.attrib['name']
      flags = 0
      for flag in filter(lambda x:x, map(lambda x: x.strip(), define.attrib.get('mode', '').split('|'))):
        flags |= getattr(re, flag)
      re_obj = re.compile(define.attrib['value'], flags)
      group = int(define.attrib.get('target', 0))
      self.regex_list[name] = {
        "re": re_obj,
        "group": group,
      }
    return

  def extract(self, input_str, debug=False):
    self.debug_mode = debug
    doc = lxml.html.fromstring(input_str)
    ret = {}
    for child in self.page:
        d = self._extract(child, doc)
        for k, v in d.iteritems():
          ret[k] = v

    return ret

  def action(self, action, dom):
    ctx = Context(self, dom)
    for s in action.split('|'):
      try:
        s = s.strip()
        func, args = function_re.match(s).groups()
        ctx.set_args(args)
        ret = functions[func](ctx)
        ctx.pipe = ret
      except:
        if self.debug_mode:
          logging.warning('Action Error: %s' % s)
        raise
    return ctx.pipe

  def select_dom(self, inst, dom, only_one = True):
    try:
      selector = inst.attrib.get('selector', None)
      if selector:
        sel = CSSSelector(selector)
        dom = sel(dom)

        if only_one:
          target = int(inst.attrib.get('order', '0'), 10)
          dom = dom[target]

      child = inst.attrib.get('child', None)
      if child:
        dom = dom[int(child, 10)]

      if self.debug_mode:
        if 'debug' in inst.attrib:
          self.debug_counter += 1
          logging.info('DOM: ', dom)
      return dom
    except:
      return None

  def _extract_field(self, inst, dom):
    dom = self.select_dom(inst, dom)
    ret = {}
    value = inst.attrib.get('value', None)
    if value:
      ret[inst.attrib['name']] = value
    else:
      ret[inst.attrib['name']] = self.action(inst.attrib['action'], dom)
    return ret

  def _extract(self, inst, dom):
    type_ = inst.tag
    try:
      if type_ == 'field':
        return self._extract_field(inst, dom)
      elif type_ == 'wrap':
        return self._extract_wrap(inst, dom)
      elif type_ == 'loop':
        return self._extract_loop(inst, dom)
    except Exception as e:
      if self.debug_mode:
        logging.warning(ET.dump(inst))
        logging.warning(dom)
      return {}

  def _extract_wrap(self, inst, dom):
    local = self.select_dom(inst, dom)
    result = {}
    if local is not None:
      for child in inst:
        d = self._extract(child, local)
        for k, v in d.iteritems():
          result[k] = v
    return result

  def _extract_loop(self, inst, dom):
    list_ = []

    valid_names = filter(lambda x: x, map(lambda x: x.strip(), inst.attrib.get('valid', '').split(',')))
    valid_flag = len(valid_names) > 0
    for ele in self.select_dom(inst, dom, only_one=False):
      tmp = {}
      for child in inst:
        d = self._extract(child, ele)
        for k, v in d.iteritems():
          tmp[k] = v
      if valid_flag:
        if not all((x in tmp for x in valid_names)):
          # if some name in valid_names not exists, skip this dom
          continue
      list_.append(tmp)

    ret = {}
    ret[inst.attrib['name']] = list_
    return ret

class DictDiffer(object):
    """
    Calculate the difference between two dictionaries as:
    (1) items added
    (2) items removed
    (3) keys same in both but changed values
    (4) keys same in both and unchanged values
    """
    def __init__(self, current_dict, past_dict):
        self.current_dict, self.past_dict = current_dict, past_dict
        self.set_current, self.set_past = set(current_dict.keys()), set(past_dict.keys())
        self.intersect = self.set_current.intersection(self.set_past)
    def added(self):
        return self.set_current - self.intersect
    def removed(self):
        return self.set_past - self.intersect
    def changed(self):
        return set(o for o in self.intersect if self.past_dict[o] != self.current_dict[o])
    def unchanged(self):
        return set(o for o in self.intersect if self.past_dict[o] == self.current_dict[o])

if __name__ == '__main__':
  extractor = Extractor('test/4.xml')

  with open('/tmp/youku.html') as f:
    txt = f.read().decode('utf-8')
  ret = extractor.extract(txt, debug=False)

  #for tweet in ret['data']:
    #print mid_to_url(tweet['id']), tweet['origin_url']
  print json.dumps(ret, ensure_ascii=False, indent=2).encode('utf-8')

  """
  import time
  for i in [1, 2]:
    begin = time.time()
    extractor = Extractor('test/%s.xml' % i)
    with open('test/%s.html' % i) as f:
      txt = f.read().decode('utf-8')
    init = time.time()
    ret = extractor.extract(txt)
    done = time.time()

    #with open('test/%i.out' %  i, 'w') as f:
      #f.write(json.dumps(ret))
    with open('test/%i.out' %  i, 'r') as f:
      std = json.load(f)

    assert ret == std
    print 'Test %s Pass' % i

    print 'Init: %s, Extract: %s, Total: %s' % (init - begin, done - init, done - begin)
  """
