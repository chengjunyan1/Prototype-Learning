import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.nn.init as init
import numpy as np
import vision as models
from pytorch_metric_learning import distances


def models_helper(name):
    assert name in ['resnet','vgg','conv','mobilenet']
    if name=='resnet': return models.resnet18(num_classes=10),512
    elif name=='vgg': return models.vgg11(num_classes=10),4096
    elif name=='mobilenet': return models.mobilenet_v3_small(num_classes=10),576
    elif name=='conv': return ConvNet(), 128 * 3 * 3

class EmbedLayer(nn.Module):
    def __init__(self,D_in,D,hid=512):
        super(EmbedLayer, self).__init__()
        self.lin = nn.Linear(D_in, hid)
        self.act=nn.PReLU()
        self.emb = nn.Linear(hid, D)
        self.dropout=nn.Dropout()
        self.apply(_weights_init)
    def forward(self,x): return self.emb(self.dropout(self.act(self.lin(x))))
    

class Vanillamodel(nn.Module):
    def __init__(self,model_name):
        super(Vanillamodel, self).__init__()
        self.net,d_f=models_helper(model_name)
        self.criterion = nn.CrossEntropyLoss().cuda()
        self.apply(_weights_init)
    def forward(self,x): return self.net(x,pred=True)
    def loss(self,x,y): return self.criterion(x, y)

class DCEmodel(nn.Module):
    def __init__(self,model_name,D=64,K=10):
        super(DCEmodel, self).__init__()
        self.net,d_f=models_helper(model_name)
        self.emb=EmbedLayer(d_f,D)
        self.criterion = nn.CrossEntropyLoss()

        self.preluip1 = nn.PReLU()
        self.ip1 = nn.Linear(D, 2)
        self.dce=dce_loss(K,2)
        self.apply(_weights_init)
    def forward(self, x, embed=False,scale=2):
        x=self.emb(self.net(x))
        features = self.preluip1(self.ip1(x))
        centers,distance=self.dce(features)
        if embed: return features,centers,distance #features, centers,distance
        return distance
    def loss(self,distance,label,features,centers,reg=0.001):  
        loss1 = self.criterion(distance, label)
        loss2=regularization(features, centers, label)
        return loss1+reg*loss2

class MLmodel(nn.Module):
    def __init__(self,mlloss,model_name,D=64,K=10):
        super(MLmodel, self).__init__()
        self.net,d_f=models_helper(model_name)
        self.emb=EmbedLayer(d_f,D)
        self.linear = nn.Linear(D, K)
        self.criterion = nn.CrossEntropyLoss().cuda()
        self.mlloss=mlloss
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        x=self.emb(self.net(x))
        pred=self.linear(x)
        if embed: return pred,x
        return pred
    def loss(self,pred,embeds,y,a=0.3,b=1e-4): 
        return self.criterion(pred, y)+self.mlloss(embeds,y,a,b)

class PLmodel(nn.Module):
    def __init__(self,model_name,C=2,D=64,lossdist='L2',normdist='L2',preddist='L2',K=10):
        super(PLmodel, self).__init__()
        self.net,d_f=models_helper(model_name)
        self.emb=EmbedLayer(d_f,D)
        self.pl=PL(C,D,lossdist,normdist,preddist,K)
        self.loss=self.pl.loss
        self.apply(_weights_init)
    def forward(self, x, embed=False):
        x=self.emb(self.net(x))
        pred,distance=self.pl.pred(x)
        if not embed: return pred
        return pred,distance,x


""" MNIST """

class ConvNet(nn.Module):
    def __init__(self):
        super(ConvNet, self).__init__()
        self.conv1_1 = nn.Conv2d(1, 32, kernel_size=5, padding=2)
        self.bn1_1 = nn.BatchNorm2d(32)
        self.prelu1_1=nn.PReLU()
        self.conv1_2 = nn.Conv2d(32, 32, kernel_size=5, padding=2)
        self.bn1_2 = nn.BatchNorm2d(32)
        self.prelu1_2=nn.PReLU()
        self.conv2_1 = nn.Conv2d(32, 64, kernel_size=5, padding=2)
        self.bn2_1 = nn.BatchNorm2d(64)
        self.prelu2_1=nn.PReLU()
        self.conv2_2 = nn.Conv2d(64, 64, kernel_size=5, padding=2)
        self.bn2_2 = nn.BatchNorm2d(64)
        self.prelu2_2=nn.PReLU()
        self.conv3_1 = nn.Conv2d(64, 128, kernel_size=5, padding=2)
        self.bn3_1 = nn.BatchNorm2d(128)
        self.prelu3_1=nn.PReLU()
        self.conv3_2 = nn.Conv2d(128, 128, kernel_size=5, padding=2)
        self.bn3_2 = nn.BatchNorm2d(128)
        self.prelu3_2=nn.PReLU()

    def forward(self, x):
        x = self.prelu1_1(self.bn1_1(self.conv1_1(x)))
        x = self.prelu1_2(self.bn1_2(self.conv1_2(x)))
        x = F.max_pool2d(x, 2)
        x = self.prelu2_1(self.bn2_1(self.conv2_1(x)))
        x = self.prelu2_2(self.bn2_2(self.conv2_2(x)))
        x = F.max_pool2d(x, 2)
        x = self.prelu3_1(self.bn3_1(self.conv3_1(x)))
        x = self.prelu3_2(self.bn3_2(self.conv3_2(x)))
        x = F.max_pool2d(x, 2)
        x= x.view(-1, 128 * 3 * 3)
        return x


""" Utils """

def _weights_init(m):
    if isinstance(m, nn.Linear) or isinstance(m, nn.Conv2d):
        init.kaiming_normal_(m.weight)
        
class dce_loss(torch.nn.Module):
    def __init__(self, K,feat_dim,init_weight=True):
        super(dce_loss, self).__init__()
        self.K=K
        self.feat_dim=feat_dim
        self.centers=nn.Parameter(torch.randn(self.feat_dim,self.K).cuda(),requires_grad=True)
        if init_weight: nn.init.kaiming_normal_(self.centers)
    def forward(self, x):
        features_square=torch.sum(torch.pow(x,2),1, keepdim=True)
        centers_square=torch.sum(torch.pow(self.centers,2),0, keepdim=True)
        features_into_centers=2*torch.matmul(x, (self.centers))
        dist=features_square+centers_square-features_into_centers
        return self.centers, -dist

def regularization(features, centers, labels):
    distance=(features-torch.t(centers)[labels])
    distance=torch.sum(torch.pow(distance,2),1, keepdim=True)
    distance=(torch.sum(distance, 0, keepdim=True))/features.shape[0]
    return distance

def dist_helper(dist):
    assert dist in ['dotproduct','L1','L2','Linf']
    if dist=='dotproduct': return distances.DotProductSimilarity()
    elif dist=='L1': return distances.LpDistance(power=1)
    elif dist=='L2': return distances.LpDistance(power=2)
    elif dist=='Linf': return distances.LpDistance(power=float('inf'))

class PL(nn.Module):
    def __init__(self,C=2,D=64,lossdist='L2',normdist='L2',preddist='L2',K=10):
        super(PL, self).__init__()
        self.embeds=nn.Parameter(
            torch.randn(C*K,D),requires_grad=True)
        self.C,self.K=C,K
        self.loss_dist=dist_helper(lossdist)
        self.norm_dist=dist_helper(normdist)
        self.pred_dist=dist_helper(preddist)
        self.apply(_weights_init)
    
    def pred(self,x):
        distance=self.loss_dist(x, self.embeds) 
        distance=distance.reshape(-1,self.C,self.K).mean(1)
        pred=-self.pred_dist(x, self.embeds)
        pred=pred.reshape(-1,self.C,self.K).mean(1)
        return pred,distance

    def loss(self,pred,x,distance,y,x_adv=None,option=[0.1,0.2]):
        a,b=option 
        normdist=self.norm_dist(x,self.embeds)
        normdist=normdist.reshape(-1,self.C,self.K).mean(1)
        y=torch.nn.functional.one_hot(y,num_classes=self.K)
        plnorm=pl_norm(y,normdist)
        plloss=pl_loss(y,distance,self.K)+a*plnorm
        if x_adv is not None:
            advdist=self.norm_dist(x_adv,self.embeds)
            advnorm=pl_norm(y,advdist,self.K)
            plloss+=b*advnorm
        return plloss

def pl_norm(y,dist): # 1 pos 0 neg
    d=gather_nd(dist,y,1).reshape(-1,1)
    return torch.mean(torch.sum(d,1))

def pl_loss(y,dist,K=10): 
    pos = gather_nd(dist,y,1).reshape(-1,1)
    neg = gather_nd(dist,y,0).reshape(-1,K-1)
    return torch.mean(torch.log(1+torch.sum(torch.exp(pos-neg),-1)))

def gather_nd(x,y,w):
    pos=torch.cat(torch.where(y==w)).reshape(2,-1)
    return x[pos[0,:], pos[1,:]]
    
    
        
        
        
