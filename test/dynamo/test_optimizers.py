"""
PYTEST_DONT_REWRITE (prevents pytest from rewriting assertions, which interferes
with test_adam in OptimizerTests)
"""
import functools

# Owner(s): ["module: dynamo"]

import inspect

import torch

import torch._dynamo
import torch._dynamo.test_case
import torch._dynamo.testing
from torch.nn import Parameter

input = torch.ones([10, 10])
model = torch.nn.Sequential(*[torch.nn.Linear(10, 10) for _ in range(2)])
model(input).sum().backward()


def make_test(optim_cls, exp_graph_count=1, closure=None, fullgraph=True, **kwargs):
    opt = optim_cls(model.parameters(), **kwargs)

    def test_fn(self):
        nonlocal opt
        if closure is not None:

            def fn():
                opt.step(closure)

        else:
            fn = opt.step

        if fullgraph and exp_graph_count == 1:
            # Calling with fullgraph=True will assert there are no graph
            # breaks, and we didn't fall back on large parts of the optimizer.
            # Many of the tests using the other branch just happened to
            # get one graph before falling back with an error and are not
            # actually working properly.
            torch.compile(fn, backend="eager", fullgraph=True)()
        else:
            _, _, graphs, _, _, _ = torch._dynamo.explain(fn)
            self.assertEqual(len(graphs), exp_graph_count)

    return test_fn


class OptimizerTests(torch._dynamo.test_case.TestCase):
    test_sgd = make_test(torch.optim.SGD, lr=0.01, exp_graph_count=1, fullgraph=False)
    # lgbfs has data-dependent control and internally iterates
    # calling the closure
    # TODO mlazos: re-enable once we have latest pytorch with FakeTensor fix #497
    # test_lbfgs = make_test(
    #    torch.optim.LBFGS, exp_frame_cnt=3, closure=lambda: model(input).sum()
    # )

    # Has data dependent control for rectification (needs symint)
    # RAdam has data-dependent control which breaks the graph;
    # furthermore, the break is inside a for loop, so we bail on the frame
    # entirely.  This is basically an xfail; if the frame count goes up
    # you done good
    test_radam = make_test(torch.optim.RAdam, exp_graph_count=0, fullgraph=False)
    test_adadelta = make_test(torch.optim.Adadelta, exp_graph_count=1, fullgraph=False)
    test_adagrad = make_test(torch.optim.Adagrad, exp_graph_count=1, fullgraph=False)
    test_adam = make_test(torch.optim.Adam, exp_graph_count=1, fullgraph=False)
    test_adamax = make_test(torch.optim.Adamax, exp_graph_count=1, fullgraph=False)
    test_adamw = make_test(torch.optim.AdamW, exp_graph_count=1, fullgraph=False)
    test_asgd = make_test(torch.optim.ASGD, exp_graph_count=1, fullgraph=False)
    test_nadam = make_test(torch.optim.NAdam, exp_graph_count=1, fullgraph=False)
    test_rmsprop = make_test(torch.optim.RMSprop, exp_graph_count=1, fullgraph=False)
    test_rprop = make_test(torch.optim.Rprop, exp_graph_count=1, fullgraph=False)


# exclude SparseAdam because other areas of the stack don't support it yet
# the others are handled specially above
exclude = {
    "Optimizer",
    "SparseAdam",  # Unsupported
    "LBFGS",  # Unsupported
}

optimizers = [
    opt
    for opt in torch.optim.__dict__.values()
    if inspect.isclass(opt)
    and issubclass(opt, torch.optim.Optimizer)
    and opt.__name__ not in exclude
]


for opt in optimizers:
    if not hasattr(OptimizerTests, "test_" + opt.__name__.lower()):
        setattr(OptimizerTests, "test_" + opt.__name__.lower(), make_test(opt))


class End2EndTests(torch._dynamo.test_case.TestCase):
    # https://github.com/pytorch/torchdynamo/issues/1604
    def test_optimizing_over_tensor_with_requires_grad(self):
        class Net(torch.nn.Module):
            def forward(self, x, y):
                z = torch.bmm(x, y)
                z = torch.flatten(z, 1)
                return z

        def training_iter_fn(batch, model, optimizer):
            optimizer.zero_grad()
            out = model(**batch)
            target = torch.tensor([0, 7])
            loss = torch.nn.CrossEntropyLoss()(out, target)
            loss.backward()
            optimizer.step()
            return loss

        net = Net()
        input1 = torch.randn(2, 1, 4)
        input2 = torch.randn(2, 4, 8, requires_grad=True)
        optimizer = torch.optim.Adam([input2], lr=0.1)

        cnts = torch._dynamo.testing.CompileCounter()
        opt_training_iter_fn = torch._dynamo.optimize(cnts)(training_iter_fn)
        batch = {"x": input1, "y": input2}
        for _ in range(2):
            opt_training_iter_fn(batch, net, optimizer)
        self.assertEqual(cnts.frame_count, 2)

    def test_state_dict(self):
        @torch.compile(backend="eager")
        def _test_state_dict(weight, bias, input):
            def fn_base(optimizer, weight, bias):
                optimizer.zero_grad()
                i = input
                loss = (weight.mv(i) + bias).pow(2).sum()
                loss.backward()
                return loss

            optimizer = torch.optim.Adagrad([weight, bias])
            fn = functools.partial(fn_base, optimizer, weight, bias)
            return optimizer, fn

        optimizer, fn = _test_state_dict(
            Parameter(torch.randn(10, 5)),
            Parameter(torch.randn(10)),
            torch.randn(5, requires_grad=True),
        )
        optimizer.step(fn)


if __name__ == "__main__":
    from torch._dynamo.test_case import run_tests

    run_tests()
