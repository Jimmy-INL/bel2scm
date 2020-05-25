import torch
import torch.nn.functional as F
import time
import pyro
from Neuirps_BEL2SCM.constants import VARIABLE_TYPE

class RegressionNet(torch.nn.Module):
	"""
	This class is used to train regression model for continuous nodes.
	"""
	def __init__(self, n_feature, n_hidden, n_output):
		super(RegressionNet, self).__init__()
		self.hidden = torch.nn.Linear(n_feature, n_hidden)   # hidden layer
		self.predict = torch.nn.Linear(n_hidden, n_output)   # output layer

	def forward(self, x):
		x = F.relu(self.hidden(x))	  # activation function for hidden layer
		x = self.predict(x)			 # linear output
		return x

class LogisticNet(torch.nn.Module):
	"""
	This class is used to train classification model for binary nodes.
	"""
	def __init__(self, n_feature, n_hidden, n_output):
		super(LogisticNet, self).__init__()
		self.hidden = torch.nn.Linear(n_feature, n_hidden)   # hidden layer
		self.predict = torch.nn.Linear(n_hidden, n_output)   # output layer

	def forward(self, x):
		x = F.relu(self.hidden(x))	  # activation function for hidden layer
		x = F.sigmoid(self.predict((x)))	 # linear output
		return x


class TrainNet():
	"""
	This class initiates RegressionNet / LogisticNet, sets hyperparameters,
	and performs training.
	"""
	# All hardcoded hyperparameter resides here.
	learning_rate = 0.01
	n_hidden = 10
	train_loss = 0
	test_loss = 0
	train_test_split_index = 2000
	n_epochs = 30
	batch_size = 128

	def __init__(self, n_feature, n_output, isRegression):
		if isRegression:
			self.net = RegressionNet(n_feature, self.n_hidden, n_output)
			self.loss_func = torch.nn.MSELoss()
		else:
			self.net = LogisticNet(n_feature, self.n_hidden, n_output)
			self.loss_func = torch.nn.BCELoss()
		self.optimizer =  torch.optim.Adam(self.net.parameters(), lr=self.learning_rate)

	def fit(self, x, y):
		train_x, train_y, test_x, test_y = self._get_train_test_data(x, y)

		for epoch in range(self.n_epochs):

			# X is a torch Variable
			permutation = torch.randperm(train_x.size()[0])

			for i in range(0, train_x.size()[0], self.batch_size):
				self.optimizer.zero_grad()
				# get batch_x, batch_y
				indices = permutation[i:i + self.batch_size]
				batch_x, batch_y = train_x[indices], train_y[indices]

				prediction = self.net(batch_x)
				loss = self.loss_func(prediction, batch_y)
				loss.backward()
				self.optimizer.step()

		# calculate train loss
		self.train_loss = self.loss_func(self.predict(train_x), train_y)
		self.test_loss = self.loss_func(self.predict(test_x), test_y)

	def predict(self, x):
		return self.net(x)

	def _get_train_test_data(self, x, y):
		# train data
		train_x = x[:self.train_test_split_index].flatten().view(-1, 1)
		train_y = y[:self.train_test_split_index]

		# test data
		test_x = x[self.train_test_split_index:].flatten().view(-1, 1)
		test_y = y[self.train_test_split_index:]

		return train_x, train_y, test_x, test_y


class ParameterEstimation:
	"""
	This class requires non-empty graph with available node_data.
	SCM class uses get_model_for_each_node function for each node after loading bel graph and data.
	"""

	def __init__(self, belgraph, config):
		# Dictionary<node_str, TrainNet obj>
		self.trained_networks = dict()

		# Dictionary <node_str, pyro.Distribution>
		self.root_distributions = dict()

		if not belgraph.nodes or not belgraph.node_data:
			raise Exception("Empty Graph or data not loaded.")

		self.belgraph = belgraph
		self.config = config

	def get_distribution_for_roots_from_data(self):

		for node_str, features_and_target_data in self.belgraph.node_data.items():

			# if node is a root node, and it has empty feature df
			if (self.belgraph.nodes[node_str].root) and (features_and_target_data["features"].empty):

				# we need node label to search for the corresponding distribution from config
				node_label = self.belgraph.nodes[node_str].node_label
				# Getting corresponding distribution from config
				node_distribution = self.config.node_label_distribution_info[node_label]

				# if the node label belongs to categorical
				if node_label in VARIABLE_TYPE["Categorical"]:
					self.root_distributions[node_str] = self._get_distribution_for_binary_root(
						node_distribution,
						features_and_target_data)

				# Otherwise we assume that the data is continuous and return a distribution with mean and std from data.
				else:
					self.root_distributions[node_str] = self._get_distribution_for_continuous_root(
						node_distribution,
						features_and_target_data)

	def get_model_for_each_non_root_node(self):
		"""
		This function iterates through every non-root node and trains a neural network.
		It pushes TrainNet objects to trained_network dictionary.
		"""
		for node_str, features_and_target_data in self.belgraph.node_data.items():

			# if node is not a root node, and it has continuous parents
			if (not self.belgraph.nodes[node_str].root) and (not features_and_target_data["features"].empty):

				# we need node label to search for the corresponding distribution from config
				node_label = self.belgraph.nodes[node_str].node_label

				# Currently, only binary classification is supported. [TODO] Add support for multi-class classification for categorical variable
				if node_label in VARIABLE_TYPE["Categorical"]:
					print("Start training of node:", node_str)
					time1 = time.time()
					trained_network = self._classification(features_and_target_data)
					print("Finished training of node:", node_str, "in ", (time.time() - time1), " seconds")
					print("Test error:", trained_network.test_loss)
				else:
					print("Start training of node:", node_str)
					time1 = time.time()
					trained_network = self._regression(features_and_target_data)
					print("Finished training of node:", node_str, "in ", (time.time() - time1), " seconds")
					print("Test error:", trained_network.test_loss)

				self.trained_networks[node_str] = trained_network

	def _regression(self, features_and_target_data):
		# convert features dataframe to float tensor.
		feature_data = torch.tensor(features_and_target_data["features"].values).float()
		number_of_features = feature_data.size()[1]

		# convert target series to float tensor.
		target_data = torch.tensor(features_and_target_data["target"].values).float()


		train_network = TrainNet(n_feature=number_of_features, n_output=1, isRegression=True)


		train_network.fit(feature_data, target_data)

		return train_network

	def _classification(self, features_and_target_data):
		feature_data = torch.tensor(features_and_target_data["features"].values).float()
		number_of_features = feature_data.size()[1]

		target_data = torch.tensor(features_and_target_data["target"].values).float()

		# new instance of TrainNet with isRegression=false.
		train_network = TrainNet(n_feature=number_of_features, n_output=1, isRegression=False)
		train_network.fit(feature_data, target_data)

		return train_network

	def _get_distribution_for_binary_root(self, node_distribution, features_and_target_data):

		# convert target series to float tensor.
		mean = torch.tensor(features_and_target_data["target"].mean())

		# mean should be a probability that becomes the parameter for Bernoulli
		if 0 <= mean <= 1:
			try:
				return node_distribution(mean)
			except:
				raise Exception(
					"The schema from _get_distribution_for_binary_root does not match for the pyro distribution for node ",
					features_and_target_data["target"].name)
		else:
			raise Exception("Something wrong with data for ", features_and_target_data["target"].name)

	def _get_distribution_for_continuous_root(self, node_distribution, features_and_target_data):

		# convert target series to float tensor.
		mean = torch.tensor(features_and_target_data["target"].mean())
		std = torch.tensor(features_and_target_data["target"].std())

		try:
			return node_distribution(mean, std)
		except:
			raise Exception(
				"The schema from _get_distribution_for_continuous_root does not match for the pyro distribution for node ",
				features_and_target_data["target"].name)
