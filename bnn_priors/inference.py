import numpy as np
from tqdm import tqdm
import torch
from .utils import get_cosine_schedule
from .sgld import SGLD
import math
from bnn_priors import prior


class SGLDRunner:
    def __init__(self, model, dataloader, epochs_per_cycle, warmup_epochs, sample_epochs, learning_rate=1e-2,
                 skip=1, temperature=1., data_mult=1., momentum=0., sampling_decay=True,
                 grad_max=1e6, cycles=1, precond_update=None, summary_writer=None):
        """
        Stochastic Gradient Langevin Dynamics for posterior sampling.

        Args:
            model (torch.Module, PriorMixin): BNN model to sample from
            num_data (int): Number of datapoints in training sest
            warmup_epochs (int): Number of epochs per cycle for warming up the Markov chain
            burnin_epochs (int): Number of epochs per cycle between warmup and sampling. When None, uses the same as warmup_steps.
            sample_epochs (int): Number of sample epochs
            learning_rate (float): Initial learning rate
            skip (int): Number of samples to skip between saved samples during the sampling phase
            temperature (float): Temperature for tempering the posterior
            data_mult (float): Effective replication of each datapoint (which is the usual approach to tempering in VI).
            momentum (float): Momentum decay parameter for SGLD
            sampling_decay (bool): Flag to control whether the learning rate should decay during sampling
            grad_max (float): maximum absolute magnitude of an element of the gradient
            cycles (int): Number of warmup and sampling cycles to perform
            precond_update (int): Number of steps after which the preconditioner should be updated. None disables the preconditioner.
            summary_writer (optional, tensorboardX.SummaryWriter): where to write the self.metrics
        """
        self.model = model
        self.dataloader = dataloader
        self.epochs_per_cycle = epochs_per_cycle
        self.descent_epochs = epochs_per_cycle - warmup_epochs - sample_epochs
        self.warmup_epochs = warmup_epochs
        self.sample_epochs = sample_epochs
        self.skip = skip
        #num_samples (int): Number of recorded per cycle
        self.num_samples = sample_epochs // skip
        self.learning_rate = learning_rate
        self.temperature = temperature
        self.data_mult = data_mult
        self.momentum = momentum
        self.sampling_decay = sampling_decay
        self.grad_max = grad_max
        self.cycles = cycles
        self.precond_update = precond_update
        self.summary_writer = summary_writer
        # TODO: is there a nicer way than adding this ".p" here?
        self._samples = {name+".p" : torch.zeros(torch.Size([self.num_samples*cycles])+param.shape)
                         for name, param in self.model.params_with_prior_dict().items()}
        self._samples["lr"] = torch.zeros(torch.Size([self.num_samples*cycles]))

        self.metrics = {}

    def run(self, progressbar=False):
        """
        Runs the sampling on the model.

        Args:
            x (torch.tensor): Training input data
            y (torch.tensor): Training labels
            progressbar (bool): Flag that controls whether a progressbar is printed
        """
        self.param_names, params = zip(*prior.named_params_with_prior(self.model))
        self.optimizer = SGLD(
            params=params,
            lr=self.learning_rate, num_data=len(self.dataloader.dataset)*self.data_mult,
            momentum=self.momentum, temperature=self.temperature)

        schedule = get_cosine_schedule(len(self.dataloader) * self.epochs_per_cycle)
        self.scheduler = torch.optim.lr_scheduler.LambdaLR(
            optimizer=self.optimizer, lr_lambda=schedule)

        epochs_since_start = -1
        for cycle in range(self.cycles):
            step = 0

            if progressbar:
                epochs = tqdm(range(self.epochs_per_cycle), position=0,
                              leave=True, desc=f"Cycle {cycle}, Sampling")
            else:
                epochs = range(self.epochs_per_cycle)

            for epoch in epochs:
                epochs_since_start += 1

                for g in self.optimizer.param_groups:
                    g['temperature'] = 0 if epoch < self.descent_epochs else self.temperature

                for (x, y) in self.dataloader:
                    self.step(step, x, y)
                    step += 1

                # TODO: should we also do this during sampling?
                if self.precond_update is not None and epoch % self.precond_update == 0:
                    # TODO: how do we actually handle minibatches here?
                    self.optimizer.estimate_preconditioner(closure=lambda x: x, K=1)

                sampling_epoch = epoch - (self.descent_epochs + self.warmup_epochs)
                if (0 <= sampling_epoch) and (sampling_epoch % self.skip == 0):
                    for name, param in self.model.params_with_prior_dict().items():
                        # TODO: is there a more elegant way than adding this ".p" here?
                        self._samples[name+".p"][(self.num_samples*cycle)+(sampling_epoch//self.skip)] = param
                    self._samples["lr"][(self.num_samples*cycle)+(sampling_epoch//self.skip)] = self.optimizer.param_groups[0]["lr"]

    def add_scalar(self, name, value, step):
        try:
            self.metrics[name].append(value)
        except KeyError:
            self.metrics[name] = []
            self.metrics[name].append(value)
        if self.summary_writer is not None:
            self.summary_writer.add_scalar(name, value, step)

    def step(self, i, x, y, lr_decay=True):
        """
        Perform one step of SGLD on the model.

        Args:
            x (torch.Tensor): Training input data
            y (torch.Tensor): Training labels
            lr_decay (bool): Flag that controls whether the learning rate should decay after this step

        Returns:
            loss (float): The current loss of the model for x and y
        """
        self.optimizer.zero_grad()
        # TODO: this only works when the full data is used,
        # otherwise the log_likelihood should be rescaled according to the batch size
        # TODO: should we multiply this by the batch size somehow?
        loss = self.model.potential_avg(x, y, temperature=self.temperature, data_mult=self.data_mult)
        loss.backward()
        for p in self.optimizer.param_groups[0]["params"]:
            p.grad.clamp_(min=-self.grad_max, max=self.grad_max)
        self.optimizer.step()
        if lr_decay:
            self.scheduler.step()

        for n, p in zip(self.param_names, self.optimizer.param_groups[0]["params"]):
            state = self.optimizer.state[p]
            self.add_scalar("preconditioner/"+n, state["preconditioner"], i)
            self.add_scalar("est_temperature/"+n, state["est_temperature"], i)
            self.add_scalar("est_config_temp/"+n, state["est_config_temp"], i)

        self.add_scalar("lr", self.optimizer.param_groups[0]["lr"], i)
        loss_ = loss.item()
        self.add_scalar("loss", loss_, i)
        if i > 0:
            self.add_scalar("log_prob_accept", self.prev_loss - loss_, i-1)

        self.prev_loss = loss_
        return loss_


    def get_samples(self):
        """
        Returns the acquired SGLD samples from the last run.

        Returns:
            samples (dict): Dictionary of torch.tensors with num_samples*cycles samples for each parameter of the model
        """
        return self._samples
