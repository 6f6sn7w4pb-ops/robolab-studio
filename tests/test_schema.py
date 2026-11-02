import math
import unittest
from robolab.schema import validate_object, validate_project

class SchemaTests(unittest.TestCase):
    def primitive(self):
        return dict(id='rl_test',kind='box',position=[0,0,.9],rotation=[0,0,0],size=[.06]*3,
                    mass=.1,friction=[1,.005,.0001],collision=True)

    def test_reject_unsafe_geometry_and_nonfinite_values(self):
        for key, value in [('mass',-1),('mass',math.nan),('size',[0,1,1]),('position',[0,math.inf,0]),
                           ('id','../other'),('kind','mesh'),('collision','false')]:
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                validate_object(dict(self.primitive(), **{key:value}))

    def test_validated_values_do_not_mutate_input(self):
        obj=self.primitive(); result=validate_object(obj)
        result['position'][0]=5
        self.assertEqual(obj['position'][0],0)

    def test_bad_schema_and_missing_task(self):
        for project in ({},{'schema_version':2},{'schema_version':1,'task':{}}):
            with self.assertRaises(ValueError): validate_project(project)

if __name__=='__main__': unittest.main(verbosity=2)
