import unittest
import torch
from bnn_priors.models import DenseNet
from bnn_priors.inference import SGLDRunner
from bnn_priors import prior


class SGLDTest(unittest.TestCase):
    def test_snelson_inference(self):
        data = np.load("../data/snelson.npz")

        device = ('cuda' if torch.cuda.is_available() else 'cpu')
        x_train = torch.from_numpy(data['x_train']).unsqueeze(1).to(device=device, dtype=torch.get_default_dtype())
        y_train = torch.from_numpy(data['y_train']).unsqueeze(1).to(x_train)

        x_test = torch.from_numpy(data['x_test']).unsqueeze(1).to(x_train)

        N_steps = 10
        skip = 50
        warmup = 500
        cycles =  5
        temperature = 1.0
        momentum = 0.9
        precond_update = 50
        lr = 5e-4

        model = DenseNet(x_train, y_train, 128, noise_std=0.5)
        model.to(x_train)
        if torch.cuda.is_available():
            model = model.cuda()   # Resample model with He initialization so SGLD works.
        model.train()

        sgld = SGLDRunner(model=model, num_samples=N_steps, warmup_steps=warmup, learning_rate=lr,
                          skip=skip, sampling_decay=True, cycles=cycles, temperature=temperature,
                          momentum=momentum, precond_update=precond_update)
        sgld.run(x=x_train, y=y_train, progressbar=True)

        assert sgld.metrics["loss"][0] > sgld.metrics["loss"][-1]
        assert sgld.metrics["lr"][0] > sgld.metrics["lr"][1]
        assert (sgld.metrics["preconditioner/latent_fn.0.weight_prior"][0]
                != sgld.metrics["preconditioner/latent_fn.0.weight_prior"][-1])