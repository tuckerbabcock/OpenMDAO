""" Unit tests for the problem interface."""

import unittest
import os
import json
import re
import types
import base64
import zlib

import errno
from shutil import rmtree
from tempfile import mkdtemp

import numpy as np

import openmdao.api as om

from openmdao.test_suite.components.sellar import SellarStateConnection
from openmdao.visualization.n2_viewer.n2_viewer import _get_viewer_data, n2
from openmdao.recorders.sqlite_recorder import SqliteRecorder
from openmdao.test_suite.test_examples.test_betz_limit import ActuatorDisc
from openmdao.utils.mpi import MPI
from openmdao.utils.shell_proc import check_call
from openmdao.utils.testing_utils import use_tempdirs
from openmdao.test_suite.test_examples.beam_optimization.multipoint_beam_group import MultipointBeamGroup

try:
    from openmdao.vectors.petsc_vector import PETScVector
except ImportError:
    PETScVector = None

# Whether to pop up a browser window for each N2
DEBUG_BROWSER = False

# set DEBUG_FILES to True if you want to view the generated HTML file(s)
DEBUG_FILES = False


def extract_compressed_model(filename):
    """
    Load an N2 html, find the compressed data string, uncompress and decode it.
    """
    file = open(filename, 'r', encoding='utf-8')
    for line in file:
        if re.search('var compressedModel', line):
            b64_data = line.replace('var compressedModel = "', '').replace('";', '')
            break

    file.close()
    compressed_data = base64.b64decode(b64_data)
    model_data = json.loads(zlib.decompress(compressed_data).decode("utf-8"))

    return model_data


def save_viewer_data(viewer_data, filename):
    """
    Save viewer data to JSON file for use in future testing.
    """
    from openmdao.utils.testing_utils import _ModelViewerDataTreeEncoder
    with open(filename, 'w') as json_file:
        json.dump(viewer_data['tree'], json_file, cls=_ModelViewerDataTreeEncoder, indent=4)


class TestViewerData(unittest.TestCase):

    def setUp(self):
        if not DEBUG_FILES:
            self.dir = mkdtemp()
            os.chdir(self.dir)
        else:
            self.dir = os.getcwd()

        self.parent_dir = os.path.dirname(os.path.realpath(__file__))

    def tearDown(self):
        if not DEBUG_FILES:
            os.chdir(self.parent_dir)
            try:
                rmtree(self.dir)
            except OSError as e:
                # If directory already deleted, keep going
                if e.errno not in (errno.ENOENT, errno.EACCES, errno.EPERM):
                    raise e

    def check_viewer_data(self, viewer_data, filename, partials=True):
        """
        Check viewer data against expected.
        """
        # check model tree from JSON file
        with open(os.path.join(self.parent_dir, filename)) as json_file:
            expected_tree = json.load(json_file)

        np.testing.assert_equal(viewer_data['tree'], expected_tree, err_msg='', verbose=True)

        # check additional items depending on the problem
        if filename.startswith('sellar'):
            expected_pathnames, expected_conns, expected_abs2prom, expected_declare_partials, \
            expected_driver_name, expected_design_vars_names, expected_responses_names = self.expected_sellar()
        elif filename.startswith('betz'):
            expected_pathnames, expected_conns, expected_abs2prom, expected_declare_partials, \
            expected_driver_name, expected_design_vars_names, expected_responses_names = self.expected_betz()

        # check expected system pathnames
        pathnames = viewer_data['sys_pathnames_list']
        self.assertListEqual(sorted(pathnames), expected_pathnames)

        # check expected connections, after mapping cycle_arrows indices back to pathnames
        connections = sorted(viewer_data['connections_list'],
                             key=lambda x: (x['tgt'], x['src']))
        expected_conns = sorted(expected_conns,
                                key=lambda x: (x['tgt'], x['src']))
        self.assertEqual(len(connections), len(expected_conns))
        for conn in connections:
            if 'cycle_arrows' in conn and conn['cycle_arrows']:
                cycle_arrows = []
                for src, tgt in conn['cycle_arrows']:
                    cycle_arrows.append(' '.join([pathnames[src], pathnames[tgt]]))
                conn['cycle_arrows'] = sorted(cycle_arrows)
        for c, ex in zip(connections, expected_conns):
            self.assertEqual(c['src'], ex['src'])
            self.assertEqual(c['tgt'], ex['tgt'])
            self.assertEqual(c.get('cycle_arrows', []), ex.get('cycle_arrows', []))

        # check expected abs2prom map
        self.assertListEqual(sorted(viewer_data['abs2prom']), sorted(expected_abs2prom))

        # check expected declare_partials_list (available after final_setup)
        if partials:
            self.assertListEqual(sorted(viewer_data['declare_partials_list']),
                                 sorted(expected_declare_partials))

        self.assertEqual(viewer_data['driver']['name'], expected_driver_name)

        self.assertListEqual(sorted(dv for dv in viewer_data['design_vars']),
                             sorted(expected_design_vars_names))
        self.assertListEqual(sorted(resp for resp in viewer_data['responses']),
                             sorted(expected_responses_names))

    def expected_sellar(self):
        pathnames = json.loads('["sub.d1", "sub.d2", "sub.state_eq_group.state_eq"]')
        conns = json.loads("""
            [
                {"src": "sub.d1.y1", "tgt": "con_cmp1.y1"},
                {"src": "sub.d2.y2", "tgt": "con_cmp2.y2"},
                {"src": "_auto_ivc.v1", "tgt": "obj_cmp.x"},
                {"src": "sub.d1.y1", "tgt": "obj_cmp.y1"},
                {"src": "sub.d2.y2", "tgt": "obj_cmp.y2"},
                {"src": "_auto_ivc.v0", "tgt": "obj_cmp.z"},
                {"src": "_auto_ivc.v1", "tgt": "sub.d1.x"},
                {"src": "sub.state_eq_group.state_eq.y2_command", "tgt": "sub.d1.y2"},
                {"src": "_auto_ivc.v0", "tgt": "sub.d1.z"},
                {"src": "sub.d1.y1", "tgt": "sub.d2.y1"},
                {"src": "_auto_ivc.v0", "tgt": "sub.d2.z"},
                {"src": "sub.d2.y2", "tgt": "sub.state_eq_group.state_eq.y2_actual", "cycle_arrows": ["sub.d1 sub.d2", "sub.state_eq_group.state_eq sub.d1"]}
            ]
        """)
        abs2prom = json.loads("""
            {
                "input": {
                    "sub.state_eq_group.state_eq.y2_actual": "state_eq.y2_actual",
                    "sub.d1.z": "z",
                    "sub.d1.x": "x",
                    "sub.d1.y2": "d1.y2",
                    "sub.d2.z": "z",
                    "sub.d2.y1": "y1",
                    "obj_cmp.x": "x",
                    "obj_cmp.y1": "y1",
                    "obj_cmp.y2": "obj_cmp.y2",
                    "obj_cmp.z": "z",
                    "con_cmp1.y1": "y1",
                    "con_cmp2.y2": "con_cmp2.y2"
                },
                "output": {
                    "sub.state_eq_group.state_eq.y2_command": "state_eq.y2_command",
                    "sub.d1.y1": "y1",
                    "sub.d2.y2": "d2.y2",
                    "obj_cmp.obj": "obj",
                    "con_cmp1.con1": "con1",
                    "con_cmp2.con2": "con2",
                    "_auto_ivc.v0": "_auto_ivc.v0",
                    "_auto_ivc.v1": "_auto_ivc.v1"
                }
            }
        """)
        declare_partials = json.loads("""
            [
                "sub.state_eq_group.state_eq.y2_command > sub.state_eq_group.state_eq.y2_actual",
                "sub.d1.y1 > sub.d1.z",
                "sub.d1.y1 > sub.d1.x",
                "sub.d1.y1 > sub.d1.y2",
                "sub.d2.y2 > sub.d2.z",
                "sub.d2.y2 > sub.d2.y1",
                "obj_cmp.obj > obj_cmp.x",
                "obj_cmp.obj > obj_cmp.y1",
                "obj_cmp.obj > obj_cmp.y2",
                "obj_cmp.obj > obj_cmp.z",
                "con_cmp1.con1 > con_cmp1.y1",
                "con_cmp2.con2 > con_cmp2.y2"
            ]
        """)

        driver_name = 'Driver'
        design_vars_names = []
        responses_names = []

        return pathnames, conns, abs2prom, declare_partials, driver_name, design_vars_names, responses_names

    def expected_betz(self):
        pathnames = json.loads('[]')
        conns = json.loads("""
            [{"src": "indeps.a", "tgt": "a_disk.a"},
             {"src": "indeps.Area", "tgt": "a_disk.Area"},
             {"src": "indeps.rho", "tgt": "a_disk.rho"},
             {"src": "indeps.Vu", "tgt": "a_disk.Vu"}]
        """)
        abs2prom = json.loads("""
            {"input": {"a_disk.a": "a", "a_disk.Area": "Area", "a_disk.rho": "rho", "a_disk.Vu": "Vu"},
            "output": {"indeps.a": "a", "indeps.Area": "Area", "indeps.rho": "rho", "indeps.Vu": "Vu",
                       "a_disk.Vr": "a_disk.Vr", "a_disk.Vd": "a_disk.Vd", "a_disk.Ct": "a_disk.Ct",
                       "a_disk.thrust": "a_disk.thrust", "a_disk.Cp": "a_disk.Cp", "a_disk.power": "a_disk.power"}}
        """)
        declare_partials = json.loads("""
            ["a_disk.Vr > a_disk.a", "a_disk.Vr > a_disk.Vu", "a_disk.Vd > a_disk.a", "a_disk.Ct > a_disk.a",
             "a_disk.thrust > a_disk.a", "a_disk.thrust > a_disk.Area", "a_disk.thrust > a_disk.rho",
             "a_disk.thrust > a_disk.Vu", "a_disk.Cp > a_disk.a", "a_disk.power > a_disk.a",
             "a_disk.power > a_disk.Area", "a_disk.power > a_disk.rho", "a_disk.power > a_disk.Vu"]
        """)

        driver_name = 'ScipyOptimizeDriver'
        design_vars_names = ['indeps.a',  'indeps.Area']
        responses_names = ['a_disk.Cp',]

        return pathnames, conns, abs2prom, declare_partials, driver_name, design_vars_names, responses_names

    def test_viewer_data(self):
        """
        Verify that the correct model structure data exists when stored as compared
        to the expected structure, using the SellarStateConnection model.

        Note: Use self.save_model_data() to regenerate JSON file if needed when updating test
        """
        filename = "sellarstate.sql"

        p = om.Problem(model=SellarStateConnection(), allow_post_setup_reorder=False)
        p.driver.add_recorder(SqliteRecorder(filename))
        p.setup()

        # there should be no values when data is generated before final_setup
        self.check_viewer_data(_get_viewer_data(p), 'sellar_no_values.json', partials=False)

        # there should be initial values when data is generated after final_setup
        p.final_setup()
        self.check_viewer_data(_get_viewer_data(p), 'sellar_initial_values.json')

        # recorded viewer data should match
        self.check_viewer_data(_get_viewer_data(filename), 'sellar_initial_values.json')

        # there should be final values when data is generated after run_model
        p.run_model()
        # TODO: need to compare with tolerance for different platforms
        # self.check_viewer_data(_get_viewer_data(p), 'sellar_final_values.json')

        # there should be no values when data is generated (after run) with values=False
        self.check_viewer_data(_get_viewer_data(p, values=False), 'sellar_no_values_run.json')

    def test_viewer_data_from_optimization_problem(self):
        """
        Verify that the correct model structure data exists when stored as compared
        to the expected structure, using the ActuatorDisc model.
        """
        prob = om.Problem()
        indeps = prob.model.add_subsystem('indeps', om.IndepVarComp(), promotes=['*'])
        indeps.add_output('a', .5)
        indeps.add_output('Area', 10.0, units='m**2')
        indeps.add_output('rho', 1.225, units='kg/m**3')
        indeps.add_output('Vu', 10.0, units='m/s')

        prob.model.add_subsystem('a_disk', ActuatorDisc(),
                                 promotes_inputs=['a', 'Area', 'rho', 'Vu'])

        # setup the optimization
        prob.driver = om.ScipyOptimizeDriver(optimizer='SLSQP')

        prob.model.add_design_var('a', lower=0., upper=1.)
        prob.model.add_design_var('Area', lower=0., upper=1.)

        # negative one so we maximize the objective
        prob.model.add_objective('a_disk.Cp', scaler=-1)

        prob.setup()
        prob.final_setup()

        # check expected viewer data
        self.check_viewer_data(_get_viewer_data(prob), 'betz_tree.json')

    def test_viewer_data_from_subgroup(self):
        """
        Test error message when asking for viewer data for a subgroup.
        """
        p = om.Problem(model=SellarStateConnection())
        p.setup()

        msg = "Viewer data is not available for sub-Group 'sub'."
        with self.assertRaises(TypeError) as cm:
            _get_viewer_data(p.model.sub)

        self.assertEqual(str(cm.exception), msg)

    def test_viewer_data_from_None(self):
        """
        Test error message when asking for viewer data for an invalid source.
        """
        msg = "Viewer data is not available for 'None'." + \
              "The source must be a Problem, model or the filename of a recording."

        with self.assertRaises(TypeError) as cm:
            _get_viewer_data(None)

        self.assertEqual(str(cm.exception), msg)

    def test_handle_ndarray_system_option(self):
        class SystemWithNdArrayOption(om.ExplicitComponent):
            def initialize(self):
                self.options.declare('arr', types=(np.ndarray,))

            def setup(self):
                self.add_input('x', val=0.0)
                self.add_output('f_x', val=0.0)

            def compute(self, inputs, outputs):
                x = inputs['x']
                outputs['f_x'] = (x - 3.0) ** 2

        prob = om.Problem()
        prob.model.add_subsystem('comp', SystemWithNdArrayOption(arr=np.ones(2)))
        prob.setup()

        viewer_data = _get_viewer_data(prob)
        np.testing.assert_equal(viewer_data['tree']['children'][1]['options']['arr'],
                                np.ones(2))


@use_tempdirs
class TestN2(unittest.TestCase):

    def test_n2_from_problem(self):
        """
        Test that an n2 html file is generated from a Problem.
        """
        p = om.Problem(model=SellarStateConnection())
        p.setup()

        html_filename = "problem_n2.html"

        for values in (None, True, False):
            with self.subTest(values=values):
                if values is not None:
                    n2(p, outfile=html_filename, show_browser=DEBUG_BROWSER, values=values)
                else:
                    n2(p, outfile=html_filename, show_browser=DEBUG_BROWSER)

                # Check that the html file has been created and has something in it.
                self.assertTrue(os.path.isfile(html_filename), f"{html_filename} is not a valid file.")
                self.assertGreater(os.path.getsize(html_filename), 100)

    def test_n2_from_model(self):
        """
        Test that an n2 html file is generated from a model.
        """
        p = om.Problem(model=SellarStateConnection())
        p.setup()

        html_filename = "model_n2.html"

        for values in (None, True, False):
            with self.subTest(values=values):
                if values is not None:
                    n2(p.model, outfile=html_filename, show_browser=DEBUG_BROWSER, values=values)
                else:
                    n2(p.model, outfile=html_filename, show_browser=DEBUG_BROWSER)

                # Check that the html file has been created and has something in it.
                self.assertTrue(os.path.isfile(html_filename), f"{html_filename} is not a valid file.")
                self.assertGreater(os.path.getsize(html_filename), 100)

    def test_n2_from_sql(self):
        """
        Test that an n2 html file is generated from a sqlite file.
        """
        sql_filename = "sellarstate.sql"

        p = om.Problem(model=SellarStateConnection())
        p.driver.add_recorder(SqliteRecorder(sql_filename))
        p.setup()
        p.final_setup()
        p.cleanup()

        for values in (None, True, False):
            with self.subTest(values=values):
                n2_from_prob_html = f"n2_from_prob_{values}.html"
                n2_from_file_html = f"n2_from_file_{values}.html"

                if values is not None:
                    n2(p, outfile=n2_from_prob_html, show_browser=DEBUG_BROWSER, values=values)
                    n2(sql_filename, outfile=n2_from_file_html, show_browser=DEBUG_BROWSER, values=values)
                else:
                    n2(p, outfile=n2_from_prob_html, show_browser=DEBUG_BROWSER)
                    n2(sql_filename, outfile=n2_from_file_html, show_browser=DEBUG_BROWSER)

                # Compare models from the files generated from the Problem and the recording
                model_data_from_prob = extract_compressed_model(n2_from_prob_html)
                model_data_from_file = extract_compressed_model(n2_from_file_html)

                self.assertDictEqual(model_data_from_prob, model_data_from_file)

                # also check data generated using n2 command
                n2_from_cmd_html = f"n2_from_cmd_{values}.html"
                if values is not False:
                    cmd = f"openmdao n2 --no_browser -o {n2_from_cmd_html} {sql_filename}"
                else:
                    cmd = f"openmdao n2 --no_values --no_browser  -o {n2_from_cmd_html} {sql_filename}"
                check_call(cmd)

                model_data_from_cmd = extract_compressed_model(n2_from_cmd_html)

                self.assertDictEqual(model_data_from_prob, model_data_from_cmd)

    def test_n2_command(self):
        """
        Check that there are no errors when running from the command line with a script.
        """
        from openmdao.test_suite.scripts import sellar
        filename = os.path.abspath(sellar.__file__).replace('.pyc', '.py')  # PY2

        browser_arg = '' if DEBUG_BROWSER else '--no_browser'

        for values_arg in ('', '--no_values'):
            with self.subTest(values_arg=values_arg):
                check_call(f'openmdao n2 {values_arg} {browser_arg} {filename}')

    def test_n2_set_title(self):
        """
        Test that an n2 html file is generated with the correct title.
        """
        p = om.Problem(model=SellarStateConnection())
        p.setup()

        html_filename = "title_n2.html"

        n2(p, outfile=html_filename, show_browser=DEBUG_BROWSER,
           title="Sellar State Connection")

        # Check that the html file has been created and has the correct title.
        self.assertTrue(os.path.isfile(html_filename),
                        f"{html_filename} is not a valid file.")

        self.assertTrue('OpenMDAO Model Hierarchy and N2 diagram: Sellar State Connection'
                        in open(html_filename, 'r', encoding='utf-8').read())

    def test_n2_connection_error(self):
        """
        Test that an n2 html file is generated from a Problem even if it has connection errors.
        """
        from openmdao.test_suite.scripts.bad_connection import BadConnectionModel

        p = om.Problem(BadConnectionModel(), name='n2_connection_error')

        msg = "\nCollected errors for problem 'n2_connection_error':" \
              "\n   'sub' <class Group>: Attempted to connect from 'tgt.x' to 'cmp.x', but " \
              "'tgt.x' is an input. All connections must be from an output to an input."

        with self.assertRaises(Exception) as cm:
            p.setup()

        self.assertEqual(cm.exception.args[0], msg)

        html_filename = "conn_n2.html"

        n2(p, outfile=html_filename, show_browser=DEBUG_BROWSER,
           title="Bad Connection")

        # Check that the html file has been created and has something in it.
        self.assertTrue(os.path.isfile(html_filename),
                        f"{html_filename} is not a valid file.")
        self.assertTrue('OpenMDAO Model Hierarchy and N2 diagram: Bad Connection'
                        in open(html_filename, 'r', encoding='utf-8').read())


@use_tempdirs
class TestUnderMPI(unittest.TestCase):
    N_PROCS = 2

    def test_non_recordable(self):
        dummyModule = types.ModuleType('dummyModule', 'The dummyModule module')

        class myComp(om.ExplicitComponent):
            def initialize(self):
                self.options.declare('foo', recordable=False)

            def setup(self):
                self.add_input('x2', distributed=True)
                self.add_output('x3', distributed=True)

            def compute(self, inputs, outputs):
                outputs['x3'] = inputs['x2'] + 1

        p = om.Problem()
        ivc = p.model.add_subsystem('ivc', om.IndepVarComp())
        ivc.add_output('x1')
        p.model.add_subsystem('myComp', myComp(foo=dummyModule))

        p.model.connect('ivc.x1', 'myComp.x2')
        p.setup()

        # Test for bug where assembling the options metadata under MPI caused a lockup when
        # they were gathered.
        n2(p, show_browser=False)

    @unittest.skipUnless(MPI and PETScVector, "MPI and PETSc are required.")
    def test_initial_value(self):
        E = 1.
        L = 1.
        b = 0.1
        volume = 0.01

        num_cp = 5
        num_elements = 50
        num_load_cases = 2

        prob = om.Problem(model=MultipointBeamGroup(E=E, L=L, b=b, volume=volume,
                                                    num_elements=num_elements, num_cp=num_cp,
                                                    num_load_cases=num_load_cases))

        prob.driver = om.ScipyOptimizeDriver(optimizer='SLSQP', tol=1e-9)
        prob.setup()
        prob.run_driver()

        h = prob['interp.h']
        expected = np.array([ 0.14122705,  0.14130706,  0.14154096,  0.1419107,   0.14238706,  0.14293095,
                              0.14349514,  0.14402636,  0.1444677,   0.14476123,  0.14485062,  0.14468388,
                              0.14421589,  0.1434107,   0.14224356,  0.14070252,  0.13878952,  0.13652104,
                              0.13392808,  0.13105565,  0.1279617,   0.12471547,  0.1213954,   0.11808665,
                              0.11487828,  0.11185599,  0.10900669,  0.10621949,  0.10338308,  0.10039485,
                              0.09716531,  0.09362202,  0.08971275,  0.08540785,  0.08070168,  0.07561313,
                              0.0701851,   0.06448311,  0.05859294,  0.05261756,  0.0466733,   0.04088557,
                              0.03538417,  0.03029845,  0.02575245,  0.02186027,  0.01872173,  0.01641869,
                              0.0150119,   0.01453876])

        assert np.linalg.norm(h - expected) < 1e-4

        def check_initial_value(subsys, parallel=False):
            """
            check that 'initial_value' is indicated for variables under a parallel group
            """
            if subsys['type'] == 'subsystem':
                # Group or Component, recurse to children
                parallel = parallel or subsys['class'] == 'openmdao.core.parallel_group:ParallelGroup'
                for child in subsys['children']:
                    check_initial_value(child, parallel)
            else:
                # input or output, check for 'initial_value' flag
                if parallel:
                    assert('initial_value' in subsys and subsys['initial_value'] is True)
                else:
                    assert('initial_value' not in subsys)

        model_data = _get_viewer_data(prob)
        for subsys in model_data['tree']['children']:
            check_initial_value(subsys)


if __name__ == "__main__":
    unittest.main()
