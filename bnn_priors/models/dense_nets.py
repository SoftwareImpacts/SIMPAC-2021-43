from .layers import Linear
from . import RegressionModel, RaoBRegressionModel
from .. import prior
from torch import nn

__all__ = ('LinearNealNormal', 'DenseNet', 'RaoBDenseNet')

def LinearNealNormal(in_dim, out_dim, std_w, std_b):
    return Linear(prior.Normal((out_dim, in_dim), 0., std_w/in_dim**.5),
                  prior.Normal((out_dim,), 0., std_b))


def LinearPrior(in_dim, out_dim, prior_w=prior.Normal, loc_w=0., std_w=1.,
                     prior_b=prior.Normal, loc_b=0., std_b=1.):
    return Linear(prior_w((out_dim, in_dim), loc_w, std_w/in_dim**.5),
                  prior_b((out_dim,), 0., std_b))


def DenseNet(in_features, out_features, width, noise_std=1.,
             prior_w=prior.Normal, loc_w=0., std_w=2**.5,
             prior_b=prior.Normal, loc_b=0., std_b=1.):
    return RegressionModel(
        nn.Sequential(
            LinearPrior(in_features, width, prior_w=prior_w, loc_w=loc_w,
                       std_w=std_w, prior_b=prior_b, loc_b=loc_b, std_b=std_b),
            nn.ReLU(),
            LinearPrior(width, width, prior_w=prior_w, loc_w=loc_w,
                       std_w=std_w, prior_b=prior_b, loc_b=loc_b, std_b=std_b),
            nn.ReLU(),
            LinearPrior(width, out_features, prior_w=prior_w, loc_w=loc_w,
                       std_w=std_w, prior_b=prior_b, loc_b=loc_b, std_b=std_b)
        ), noise_std)


def RaoBDenseNet(x_train, y_train, width, noise_std=1.):
    # TODO: also add priors here
    in_dim = x_train.size(-1)
    out_dim = y_train.size(-1)
    return RaoBRegressionModel(
        x_train, y_train, noise_std,
        last_layer_std=(2/width)**.5,
        net=nn.Sequential(
            LinearNealNormal(in_dim, width, 2**.5, 1.0),
            nn.ReLU(),
            LinearNealNormal(width, width, 2**.5, 1.0),
            nn.ReLU()))